"""Ota-onalarga avtomatik xabarlar umumiy Yurtora boti orqali: davomat, to'lov, oylik hisobot.

Har bir markaz o'z sxemasida ishlaydi; xabarlar NotificationLog navbatiga tushadi va botning
yuborish aylanishi (routing.send_pending_everywhere) ularni yuboradi. Veb-so'rov Telegram'ni kutmaydi.

- Davomat: faqat "Kelmadi" va "Kechikdi". ATTENDANCE_DELAY dan keyin yuboriladi va yuborish paytida
  belgi qayta tekshiriladi: o'qituvchi xatoni tuzatgan bo'lsa ("Keldi"), xabar bekor qilinadi.
- To'lov: kassa to'lovni saqlagandan keyin darhol.
- Oylik hisobot: har oyning 1-3 kunlari, o'tgan oy uchun davomat va balans.
"""

import logging
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import timezone

from apps.core.branding import center_name
from apps.core.months import add_months, month_label, month_start

from .models import NotificationLog, NotifySettings, TelegramContact

logger = logging.getLogger(__name__)

ATTENDANCE_DELAY = timedelta(minutes=10)
ATTENDANCE_MAX_AGE_DAYS = 1  # eski darslar uchun (orqaga qarab belgilash) xabar yuborilmaydi
MONTHLY_HOUR = 10  # Toshkent vaqti
MONTHLY_LAST_DAY = 3  # oyning shu kunigacha yuborilmagan bo'lsa, keyin yuborilmaydi
ATTENDANCE_STATUSES = ("absent", "late")


def money(amount):
    return f"{abs(int(amount)):,}".replace(",", " ")


def _queue(student, kind, ref, text, send_after=None):
    """O'quvchining barcha faol chatlariga navbat yozuvi. Navbatga qo'yilganlar soni."""
    queued = 0
    for chat_id in TelegramContact.objects.filter(student=student, is_active=True).values_list("chat_id", flat=True):
        try:
            with transaction.atomic():
                NotificationLog.objects.create(
                    student=student, chat_id=chat_id, kind=kind, ref=ref, text=text,
                    status=NotificationLog.Status.PENDING, send_after=send_after,
                )
            queued += 1
        except IntegrityError:
            pass  # shu voqea shu chat uchun allaqachon navbatda
    return queued


def _safe(func):
    """Xabar tayyorlashdagi xato hech qachon davomat yoki to'lovni buzmasin (on_commit ichida chaqiriladi)."""

    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception:  # noqa: BLE001
            logger.exception("Ota-onaga xabar navbatga qo'yilmadi: %s", func.__name__)
            return 0

    wrapper.__name__ = func.__name__
    wrapper.__doc__ = func.__doc__
    return wrapper


# --- Davomat ----------------------------------------------------------------


def attendance_text(attendance):
    lesson = attendance.lesson
    when = f"{lesson.date:%d.%m.%Y} {lesson.start_time:%H:%M}"
    what = "darsga kelmadi" if attendance.status == "absent" else "darsga kechikib keldi"
    return (
        f"«{center_name()}»: davomat.\n"
        f"{attendance.student.full_name} {when} «{lesson.group.name}» {what}.\n"
        "Savollar bo'lsa, administratorga murojaat qiling."
    )


@_safe
def on_attendance(attendance_id):
    from apps.attendance.models import Attendance

    attendance = Attendance.objects.select_related("lesson__group", "student").filter(pk=attendance_id).first()
    if attendance is None or attendance.status not in ATTENDANCE_STATUSES:
        return 0  # "Keldi" uchun xabar yo'q; navbatdagisi yuborish paytida bekor qilinadi
    if not NotifySettings.get().attendance:
        return 0
    if attendance.lesson.date < timezone.localdate() - timedelta(days=ATTENDANCE_MAX_AGE_DAYS):
        return 0
    return _queue(
        attendance.student, NotificationLog.Kind.ATTENDANCE, f"att{attendance.pk}",
        "Davomat xabari (yuborishda tayyorlanadi)", send_after=timezone.now() + ATTENDANCE_DELAY,
    )


def current_attendance_text(ref):
    """Yuborish paytidagi matn yoki None (belgi o'zgargan yoki o'chirilgan: xabar bekor)."""
    from apps.attendance.models import Attendance

    try:
        pk = int(ref.removeprefix("att"))
    except ValueError:
        return None
    attendance = Attendance.objects.select_related("lesson__group", "student").filter(pk=pk).first()
    if attendance is None or attendance.status not in ATTENDANCE_STATUSES:
        return None
    return attendance_text(attendance)


# --- To'lov -----------------------------------------------------------------


def payment_text(entry):
    from apps.finance.services import get_balance

    lines = [
        f"«{center_name()}»: to'lov qabul qilindi. Rahmat!",
        f"O'quvchi: {entry.student.full_name}",
    ]
    if entry.group:
        lines.append(f"Guruh: {entry.group.name}")
    amount = f"Summa: {money(entry.amount)} so'm"
    if entry.period:
        amount += f" ({month_label(entry.period)} uchun)"
    lines += [amount, f"Sana: {entry.entry_date:%d.%m.%Y}"]
    if entry.group:
        balance = get_balance(entry.student, entry.group)
        if balance < 0:
            lines.append(f"Qolgan qarz: {money(balance)} so'm")
        elif balance > 0:
            lines.append(f"Oldindan to'langan: {money(balance)} so'm")
        else:
            lines.append("Qarz yo'q.")
    return "\n".join(lines)


@_safe
def on_payment(entry_id):
    from apps.finance.models import LedgerEntry

    entry = LedgerEntry.objects.select_related("student", "group").filter(pk=entry_id, kind="payment").first()
    if entry is None or entry.student is None or not NotifySettings.get().payment:
        return 0
    return _queue(entry.student, NotificationLog.Kind.PAYMENT, f"pay{entry.pk}", payment_text(entry))


# --- Oylik hisobot ----------------------------------------------------------


def monthly_text(student, month):
    """O'tgan oy: har guruh bo'yicha davomat va hozirgi balans."""
    from apps.attendance.models import Attendance
    from apps.finance.services import get_balance
    from apps.groups.models import Enrollment

    next_month = add_months(month, 1)
    lines = [f"«{center_name()}»: {month_label(month)} hisoboti", f"O'quvchi: {student.full_name}", ""]
    enrollments = (
        Enrollment.objects.filter(student=student, status__in=["active", "frozen", "trial"])
        .select_related("group")
        .order_by("group__name")
    )
    for enrollment in enrollments:
        stats = Attendance.objects.filter(
            student=student, lesson__group=enrollment.group, lesson__status="held",
            lesson__date__gte=month, lesson__date__lt=next_month,
        ).aggregate(
            total=Count("id"),
            present=Count("id", filter=Q(status="present")),
            late=Count("id", filter=Q(status="late")),
            absent=Count("id", filter=Q(status="absent")),
            excused=Count("id", filter=Q(status="excused")),
        )
        lines.append(f"• {enrollment.group.name}")
        if stats["total"]:
            percent = round((stats["present"] + stats["late"]) * 100 / stats["total"])
            lines.append(
                f"  Davomat: {percent}% — keldi {stats['present']}, kechikdi {stats['late']}, "
                f"kelmadi {stats['absent']}, sababli {stats['excused']}"
            )
        else:
            lines.append("  Bu oyda belgilangan dars yo'q.")
        balance = get_balance(student, enrollment.group)
        if balance < 0:
            lines.append(f"  To'lov: qarz {money(balance)} so'm")
        elif balance > 0:
            lines.append(f"  To'lov: oldindan {money(balance)} so'm")
        else:
            lines.append("  To'lov: qarz yo'q")
    lines += ["", "Savollar bo'lsa, administratorga murojaat qiling."]
    return "\n".join(lines)


def maybe_send_monthly_reports(now=None):
    """Oyning 1-3 kunlari soat 10:00 dan keyin bir marta: o'tgan oy hisoboti. Navbatga qo'yilganlar soni."""
    local_now = (now or timezone.now()).astimezone(ZoneInfo(settings.TIME_ZONE))
    if local_now.day > MONTHLY_LAST_DAY or local_now.hour < MONTHLY_HOUR:
        return 0
    if not NotifySettings.get().monthly:
        return 0
    month = add_months(month_start(local_now.date()), -1)
    ref = f"m{month:%Y%m}"
    if NotificationLog.objects.filter(kind=NotificationLog.Kind.MONTHLY, ref=ref).exists():
        return 0  # bu oy uchun allaqachon tayyorlangan

    from apps.students.models import Student

    queued = 0
    students = Student.objects.filter(
        telegram_contacts__is_active=True,
        enrollments__status__in=["active", "frozen", "trial"],
    ).distinct()
    for student in students:
        queued += _queue(student, NotificationLog.Kind.MONTHLY, ref, monthly_text(student, month))
    return queued
