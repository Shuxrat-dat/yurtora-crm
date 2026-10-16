"""Kunlik statistika: tanlangan kun uchun tushum, davomat va yangi o'quvchilar.

Barcha ko'rsatkichlar mavjud modellardan hisoblanadi (yangi model yo'q).
Huquqlar:
- direktor: moliya, davomat, yangi o'quvchilar;
- administrator: davomat, yangi o'quvchilar (moliyaviy hisobotsiz);
- o'qituvchi: faqat o'z darslari bo'yicha davomat.
"""

from collections import defaultdict
from datetime import date, timedelta

from django.db.models import Q, Sum
from django.utils import timezone

from apps.accounts.models import User
from apps.attendance.models import Attendance
from apps.finance.models import LedgerEntry
from apps.groups.models import Enrollment
from apps.schedule.models import Lesson
from apps.students.models import Student
from apps.teachers.models import Teacher

PERIOD_YESTERDAY = "yesterday"
PERIOD_TODAY = "today"
PERIOD_DATE = "date"

# Tizim qo'llab-quvvatlaydigan to'lov usullari (LedgerEntry.PaymentMethod) va ularning yorliqlari
METHOD_LABELS = {
    LedgerEntry.PaymentMethod.CASH: "Naqd pul",
    LedgerEntry.PaymentMethod.CLICK: "Click orqali",
    LedgerEntry.PaymentMethod.CARD: "Plastik karta",
    LedgerEntry.PaymentMethod.PAYME: "Payme orqali",
    LedgerEntry.PaymentMethod.UZUM: "Uzum orqali",
    LedgerEntry.PaymentMethod.TRANSFER: "Pul o‘tkazmasi",
}
# Summa 0 bo'lsa ham doim ko'rsatiladigan usullar
ALWAYS_SHOWN_METHODS = (LedgerEntry.PaymentMethod.CASH, LedgerEntry.PaymentMethod.CLICK)
OTHER_METHOD_LABEL = "Boshqa"

# O'quvchi darsga kelmagan hisoblanadigan davomat holatlari
ABSENT_STATUSES = (Attendance.Status.ABSENT, Attendance.Status.EXCUSED)


def resolve_period(period, raw_date="", today=None):
    """Davr nomi va sanadan (kun) aniq sanani qaytaradi. Noto'g'ri qiymat: kecha."""
    today = today or timezone.localdate()
    if period == PERIOD_TODAY:
        return PERIOD_TODAY, today
    if period == PERIOD_DATE:
        try:
            chosen = date.fromisoformat(raw_date)
        except (TypeError, ValueError):
            chosen = None
        if chosen is not None:
            return PERIOD_DATE, min(chosen, today)
    return PERIOD_YESTERDAY, today - timedelta(days=1)


def get_daily_stats(user, day):
    """Foydalanuvchi huquqlariga mos kunlik statistika."""
    is_director = user.role == User.Role.DIRECTOR
    is_administrator = user.role == User.Role.ADMINISTRATOR
    return {
        "date": day,
        "finance": _finance_stats(day) if is_director else None,
        "new_students": (
            Student.objects.filter(joined_date=day).count()
            if is_director or is_administrator
            else None
        ),
        "attendance": _attendance_stats(user, day),
    }


def _finance_stats(day):
    """Kun uchun haqiqiy tushum (faqat 'payment' yozuvlari) va usullar bo'yicha taqsimot."""
    rows = (
        LedgerEntry.objects.filter(kind__in=[LedgerEntry.Kind.PAYMENT, LedgerEntry.Kind.REFUND], entry_date=day)
        .values("method")
        .annotate(total=Sum("amount"))
    )
    by_method = {row["method"]: row["total"] or 0 for row in rows}

    methods = []
    for code, label in METHOD_LABELS.items():
        amount = by_method.get(code, 0)
        if amount or code in ALWAYS_SHOWN_METHODS:
            methods.append({"label": label, "amount": amount})

    other_amount = sum(
        amount for code, amount in by_method.items() if code not in METHOD_LABELS
    )
    if other_amount:
        methods.append({"label": OTHER_METHOD_LABEL, "amount": other_amount})

    # Jami = usullar yig'indisi (har bir yozuv bir marta hisoblanadi)
    return {"total": sum(by_method.values()), "methods": methods}


def _attendance_stats(user, day):
    """Kelmagan va davomat belgilanmagan o'quvchilar, hamda darslar bo'yicha taqsimot.

    Hali o'tkazilmagan (held bo'lmagan) darslar uchun 'belgilanmagan' hisoblanmaydi.
    """
    lessons = (
        Lesson.objects.filter(date=day)
        .exclude(status=Lesson.Status.CANCELLED)
        .select_related("group")
        .order_by("start_time", "group__name")
    )
    if user.role == User.Role.TEACHER:
        teacher = Teacher.objects.filter(user=user).first()
        lessons = lessons.filter(teacher=teacher) if teacher else lessons.none()
    lessons = list(lessons)

    empty = {"rows": [], "absent_total": 0, "unmarked_total": 0}
    if not lessons:
        return empty

    marked = defaultdict(set)
    absent = defaultdict(set)
    attendance_rows = Attendance.objects.filter(
        lesson_id__in=[lesson.pk for lesson in lessons]
    ).values_list("lesson_id", "student_id", "status")
    for lesson_id, student_id, status in attendance_rows:
        marked[lesson_id].add(student_id)
        if status in ABSENT_STATUSES:
            absent[lesson_id].add(student_id)

    # Davomat kutilayotgan o'quvchilar: dars sanasida faol a'zolik va faol holat
    held_group_ids = {
        lesson.group_id for lesson in lessons if lesson.status == Lesson.Status.HELD
    }
    expected = defaultdict(set)
    if held_group_ids:
        enrollments = (
            Enrollment.objects.filter(
                group_id__in=held_group_ids,
                status=Enrollment.Status.ACTIVE,
                start_date__lte=day,
                student__status=Student.Status.ACTIVE,
            )
            .filter(Q(end_date__isnull=True) | Q(end_date__gte=day))
            .values_list("group_id", "student_id")
        )
        for group_id, student_id in enrollments:
            expected[group_id].add(student_id)

    rows = []
    absent_all = set()
    unmarked_all = set()
    for lesson in lessons:
        is_held = lesson.status == Lesson.Status.HELD
        lesson_marked = marked.get(lesson.pk, set())
        if not is_held and not lesson_marked:
            continue  # hali o'tilmagan va hech narsa belgilanmagan dars ko'rsatilmaydi
        absent_ids = absent.get(lesson.pk, set())
        unmarked_ids = (
            expected.get(lesson.group_id, set()) - lesson_marked if is_held else set()
        )
        absent_all |= absent_ids
        unmarked_all |= unmarked_ids
        rows.append(
            {
                "group": lesson.group.name,
                "start_time": lesson.start_time,
                "end_time": lesson.end_time,
                "absent": len(absent_ids),
                "unmarked": len(unmarked_ids),
                "is_held": is_held,
            }
        )

    return {
        "rows": rows,
        "absent_total": len(absent_all),
        "unmarked_total": len(unmarked_all),
    }
