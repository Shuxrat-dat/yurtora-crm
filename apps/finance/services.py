"""Finance business logic services."""

import logging
from collections import defaultdict
from datetime import date
from typing import TYPE_CHECKING

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum

from apps.accounts.models import User
from apps.accounts.permissions import user_has_role
from apps.core.filters import person_search_q
from apps.finance.models import LedgerEntry
from apps.groups.models import Enrollment
from apps.schedule.models import Lesson
from apps.students.models import Student

if TYPE_CHECKING:
    from apps.finance.models import PayrollAdjustment, PayrollMonth, SalaryPayout

# Moliya va ish haqi (FOT) faqat direktor uchun.
DIRECTOR_ONLY = [User.Role.DIRECTOR]
# Administrator to'lovni qabul qiladi va chek chiqaradi, boshqa moliyaviy amallarni ko'rmaydi.
PAYMENT_ROLES = [User.Role.ADMINISTRATOR, User.Role.DIRECTOR, User.Role.ACCOUNTANT]


logger = logging.getLogger(__name__)


# Tushum hisobotlari uchun: to'lovlar (plus) va qaytarilgan pullar (minus) — sof tushum
INCOME_KINDS = [LedgerEntry.Kind.PAYMENT, LedgerEntry.Kind.REFUND]


class DuplicatePayment(ValidationError):
    """Bir xil kalit bilan to'lov allaqachon qabul qilingan (formani takroriy yuborish)."""


def lesson_charge_amount(group, lesson: Lesson) -> int:
    """
    Guruh va dars uchun to'lov miqdorini hisoblaydi (deterministik, toza funksiya).

    Formula: floor(P*i/n) - floor(P*(i-1)/n)
    bu yerda:
    - P = monthly_price (kurs oylik narxi)
    - n = o'sha kalendar oyidagi guruhdagi bekor qilinmagan darslar soni
    - i = darsning tartib raqami (1 dan n gacha, sana bo'yicha saralangan)

    Barcha n darslar uchun yig'indi rov no P bo'ladi.
    """
    year = lesson.date.year
    month = lesson.date.month
    monthly_price = group.course.monthly_price

    # O'sha oydagi barcha darslar (cancelled dan tashqari), sana bo'yicha saralangan
    month_lessons = list(
        Lesson.objects.filter(
            group=group,
            date__year=year,
            date__month=month,
        )
        .exclude(status=Lesson.Status.CANCELLED)
        .order_by("date", "start_time")
    )

    if not month_lessons:
        return 0

    n = len(month_lessons)

    # Darsning tartib raqamini topish (1-indexed)
    try:
        i = month_lessons.index(lesson) + 1
    except ValueError:
        # Dars ro'yxatda yo'q (cancelled yoki boshqa oyga tegishli)
        return 0

    # Deterministik formula
    charge = (monthly_price * i // n) - (monthly_price * (i - 1) // n)

    return -charge  # Manfiy, chunki bu to'lov hisobi (charge)


def _month_lessons(group, lesson: Lesson) -> list[Lesson]:
    """Darsning kalendar oyidagi guruhning bekor qilinmagan darslari (sana bo'yicha)."""
    return list(
        Lesson.objects.filter(group=group, date__year=lesson.date.year, date__month=lesson.date.month)
        .exclude(status=Lesson.Status.CANCELLED)
        .order_by("date", "start_time")
    )


def _eligible(enrollment, day) -> bool:
    """A'zolik shu sanadagi dars uchun to'lanadimi (paid_from va muzlatish davri hisobga olinadi)."""
    if enrollment.paid_from is None or enrollment.paid_from > day:
        return False
    return enrollment.frozen_from is None or enrollment.frozen_from > day


def _settle_amount(enrollment, lesson, month_lessons, monthly_price, base_amount) -> int:
    """
    Oyning oxirgi darsida (oydagi barcha bekor qilinmagan darslar o'tilgan) yig'indi aniq
    floor(P*j/n) ga tenglashtiriladi: darslar soni oy o'rtasida o'zgargan bo'lsa ham (bekor qilish,
    qo'shish), oylik to'lov narxdan kam yoki ko'p yig'ilib qolmaydi. Aks holda — oddiy formula.
    """
    if any(item.status != Lesson.Status.HELD for item in month_lessons):
        return base_amount
    eligible_lessons = [item for item in month_lessons if _eligible(enrollment, item.date)]
    already = LedgerEntry.objects.filter(
        enrollment=enrollment, kind=LedgerEntry.Kind.CHARGE, lesson__in=eligible_lessons
    ).count()
    if already + 1 != len(eligible_lessons):
        return base_amount  # bu oyning oxirgi hisoblanmagan darsi emas
    held_eligible = len(eligible_lessons)
    target = monthly_price * held_eligible // len(month_lessons)
    charged = -(
        LedgerEntry.objects.filter(
            enrollment=enrollment,
            kind=LedgerEntry.Kind.CHARGE,
            entry_date__year=lesson.date.year,
            entry_date__month=lesson.date.month,
        ).aggregate(total=Sum("amount"))["total"]
        or 0
    )
    return -max(target - charged, 0)


def accrue_lesson_charges(lesson: Lesson, only_enrollment=None) -> list[LedgerEntry]:
    """
    Dars uchun barcha faol enrollmentlarga to'lovlarni hisoblaydi (idempotent).

    Qoidalar:
    - Dars status=held bo'lishi kerak.
    - Faqat status=active bo'lgan va paid_from <= lesson.date bo'lgan enrollmentlarga
      hisoblanadi. trial, frozen, finished statuslariga hisoblanmaydi; arxivlangan o'quvchiga ham.
    - Dars sanasi muzlatish davrida bo'lgan bo'lsa, hisoblash o'tkazilmaydi
      (frozen_from mavjud va lesson.date >= frozen_from).
    - Takroriy chaqiruv dublikat yaratmaydi (UniqueConstraint orqali).
    - To'lov miqdori deterministik formula orqali hisoblanadi; oyning oxirgi darsida oylik narxga tenglashtiriladi.
    - only_enrollment: faqat bitta a'zolik uchun (orqaga qarab yozilgan o'quvchiga hisob-kitob).
    """
    if lesson.status != Lesson.Status.HELD:
        raise ValidationError(
            "Faqat o'tkazilgan (held) darslar uchun to'lovlarni hisoblash mumkin."
        )
    return _charge_enrollments(lesson, only_enrollment)


def _charge_enrollments(lesson: Lesson, only_enrollment=None) -> list[LedgerEntry]:
    """
    Dars uchun to'lov yozuvlarini yaratadi (status tekshirilmaydi: chaqiruvchi javobgar).
    accrue_lesson_charges (held dars) va backfill_enrollment_charges (o'tgan, lekin belgilanmagan dars)
    shu yerdan foydalanadi.
    """
    base_amount = lesson_charge_amount(lesson.group, lesson)

    if base_amount == 0:
        return []

    # Faol enrollmentlar: paid_from <= lesson.date
    # Va muzlatish davrida bo'lmagan (frozen_from null yoki lesson.date < frozen_from)
    from django.db.models import Q

    active_enrollments = Enrollment.objects.filter(
        Q(group=lesson.group)
        & Q(status=Enrollment.Status.ACTIVE)
        & ~Q(student__status=Student.Status.ARCHIVED)
        & Q(paid_from__isnull=False)
        & Q(paid_from__lte=lesson.date)
        & (Q(frozen_from__isnull=True) | Q(frozen_from__gt=lesson.date))
    ).select_related("student")
    if only_enrollment is not None:
        active_enrollments = active_enrollments.filter(pk=only_enrollment.pk)

    month_lessons = _month_lessons(lesson.group, lesson)
    monthly_price = lesson.group.course.monthly_price
    created_entries = []

    with transaction.atomic():
        for enrollment in active_enrollments:
            if LedgerEntry.objects.filter(
                enrollment=enrollment, lesson=lesson, kind=LedgerEntry.Kind.CHARGE
            ).exists():
                continue  # allaqachon hisoblangan
            amount = _settle_amount(enrollment, lesson, month_lessons, monthly_price, base_amount)
            if amount == 0:
                continue
            try:
                # Har yozuv o'z savepoint'ida: bir vaqtdagi dublikat PostgreSQL tranzaksiyasini buzmaydi
                with transaction.atomic():
                    created_entries.append(
                        LedgerEntry.objects.create(
                            student=enrollment.student,
                            group=lesson.group,
                            enrollment=enrollment,
                            lesson=lesson,
                            kind=LedgerEntry.Kind.CHARGE,
                            amount=amount,
                            entry_date=lesson.date,
                            comment=f"Dars to'lovi: {lesson.date}",
                        )
                    )
            except IntegrityError:
                pass  # boshqa jarayon allaqachon yozgan

    return created_entries


def backfill_enrollment_charges(enrollment, today=None) -> int:
    """
    Orqaga qarab yozilgan a'zolik (paid_from o'tmishda) uchun o'tgan darslarni hisoblaydi:
    o'tilgan (held) darslar va jadval bo'yicha o'tib ketgan, lekin belgilanmagan (scheduled, sana < bugun)
    darslar. Bekor qilingan darslar hisoblanmaydi. Dars holati o'zgarmaydi va to'lov faqat shu a'zolikka
    yoziladi: keyin dars "o'tildi" deb belgilansa, takroriy yozuv yaratilmaydi.
    Yaratilgan yozuvlar sonini qaytaradi.
    """
    from django.db.models import Q
    from django.utils import timezone

    if enrollment.status != Enrollment.Status.ACTIVE or enrollment.paid_from is None:
        return 0
    today = today or timezone.localdate()
    lessons = Lesson.objects.filter(group=enrollment.group, date__gte=enrollment.paid_from).filter(
        Q(status=Lesson.Status.HELD) | Q(status=Lesson.Status.SCHEDULED, date__lt=today)
    ).order_by("date", "start_time")
    created = 0
    with transaction.atomic():
        for lesson in lessons:
            created += len(_charge_enrollments(lesson, only_enrollment=enrollment))
    return created


def reverse_lesson_charges(lesson: Lesson, user=None, comment: str = "") -> int:
    """
    Darsning hisoblangan to'lovlarini qaytaradi (musbat tuzatish, `reverses` orqali bog'lanadi).
    Allaqachon qaytarilgan (masalan, sababli kelmagan uchun) yozuvlar o'tkazib yuboriladi. Soni qaytariladi.
    """
    from django.utils import timezone

    charges = LedgerEntry.objects.filter(lesson=lesson, kind=LedgerEntry.Kind.CHARGE, reversed_by__isnull=True)
    count = 0
    with transaction.atomic():
        for charge in charges:
            LedgerEntry.objects.create(
                student=charge.student,
                group=charge.group,
                enrollment=charge.enrollment,
                lesson=lesson,
                kind=LedgerEntry.Kind.ADJUSTMENT,
                amount=-charge.amount,
                entry_date=timezone.localdate(),
                comment=comment or f"Dars bekor qilindi: hisob qaytarildi ({lesson.date})",
                created_by=user,
                reverses=charge,
            )
            count += 1
    return count


def register_payment(
    student,
    group,
    amount: int,
    method: str,
    user,
    entry_date: date,
    comment: str = "",
    idempotency_key: str | None = None,
) -> LedgerEntry:
    """
    To'lovni ro'yxatga oladi.

    idempotency_key: formadan keladigan bir martalik kalit. Shu kalit bilan to'lov allaqachon bo'lsa,
    DuplicatePayment ko'tariladi va ikkinchi yozuv yaratilmaydi (qo'sh bosish, sahifani qayta yuborish).

    Huquqlar: administrator va director.
    amount musbat bo'lishi kerak.
    entry_date yopilgan oyga to'g'ri kelmasligi kerak.
    """
    if amount <= 0:
        raise ValidationError("To'lov summasi musbat bo'lishi kerak.")

    if user is not None and not user_has_role(user, PAYMENT_ROLES):
        raise PermissionDenied(
            "Faqat administrator va direktor to'lovni ro'yxatga olishi mumkin."
        )

    if method not in dict(LedgerEntry.PaymentMethod.choices):
        raise ValidationError(f"Noto'g'ri to'lov usuli: {method}")

    # Проверка: entry_date не в закрытом месяце
    from apps.finance.models import PayrollMonth

    try:
        payroll_month = PayrollMonth.objects.get(
            year=entry_date.year, month=entry_date.month
        )
        if payroll_month.is_closed:
            from django.utils import timezone

            raise ValidationError(
                f"Oy {entry_date.year}-{entry_date.month:02d} yopilgan. "
                f"Yopilgan oyga to'lov kiritish mumkin emas. "
                f"Iltimos, joriy sanani ({timezone.localdate()}) ishlating yoki "
                f"oylik hisobni qayta ochish uchun direktorga murojaat qiling."
            )
    except PayrollMonth.DoesNotExist:
        # Месяц не закрыт, можно продолжать
        pass

    key = (idempotency_key or "").strip()[:64] or None
    if key and LedgerEntry.objects.filter(idempotency_key=key).exists():
        raise DuplicatePayment("Bu to'lov allaqachon qabul qilingan.")

    try:
        with transaction.atomic():
            return LedgerEntry.objects.create(
                student=student,
                group=group,
                kind=LedgerEntry.Kind.PAYMENT,
                amount=amount,
                method=method,
                entry_date=entry_date,
                comment=comment,
                created_by=user,
                idempotency_key=key,
            )
    except IntegrityError:
        if key and LedgerEntry.objects.filter(idempotency_key=key).exists():
            raise DuplicatePayment("Bu to'lov allaqachon qabul qilingan.") from None  # bir vaqtdagi ikki so'rov
        raise


def register_refund(
    student,
    group,
    amount: int,
    method: str,
    user,
    entry_date: date,
    comment: str,
    idempotency_key: str | None = None,
) -> LedgerEntry:
    """
    O'quvchiga pulni qaytarishni ro'yxatga oladi (kitobda manfiy yozuv, kassadan chiqim).

    Huquqlar: faqat direktor. Izoh majburiy. Qaytariladigan summa guruhdagi ortiqcha to'lovdan
    (musbat balansdan) oshmasligi kerak: qarzdor o'quvchiga "qaytarib" bo'lmaydi.
    """
    if amount <= 0:
        raise ValidationError("Qaytariladigan summa musbat bo'lishi kerak.")
    if not comment or not comment.strip():
        raise ValidationError("Pul qaytarish uchun izoh majburiy.")
    if user is not None and not user_has_role(user, DIRECTOR_ONLY):
        raise PermissionDenied("Faqat direktor pul qaytarishi mumkin.")
    if method not in dict(LedgerEntry.PaymentMethod.choices):
        raise ValidationError(f"Noto'g'ri to'lov usuli: {method}")

    from apps.finance.models import PayrollMonth

    payroll_month = PayrollMonth.objects.filter(year=entry_date.year, month=entry_date.month).first()
    if payroll_month and payroll_month.is_closed:
        raise ValidationError(f"Oy {entry_date.year}-{entry_date.month:02d} yopilgan: shu oyga yozuv kiritib bo'lmaydi.")

    key = (idempotency_key or "").strip()[:64] or None
    if key and LedgerEntry.objects.filter(idempotency_key=key).exists():
        raise DuplicatePayment("Bu qaytarish allaqachon qayd etilgan.")

    try:
        with transaction.atomic():
            # O'quvchi qatorini qulflaymiz: ikki bir vaqtdagi qaytarish balansdan oshib ketmasligi uchun
            Student.objects.select_for_update().get(pk=student.pk)
            balance = get_balance(student, group)
            if amount > max(balance, 0):
                raise ValidationError(
                    f"Qaytarish mumkin bo'lgan summa: {max(balance, 0):,} so'm (guruhdagi ortiqcha to'lov). "
                    f"Kiritilgan: {amount:,} so'm."
                )
            return LedgerEntry.objects.create(
                student=student,
                group=group,
                kind=LedgerEntry.Kind.REFUND,
                amount=-amount,
                method=method,
                entry_date=entry_date,
                comment=comment.strip(),
                created_by=user,
                idempotency_key=key,
            )
    except IntegrityError:
        if key and LedgerEntry.objects.filter(idempotency_key=key).exists():
            raise DuplicatePayment("Bu qaytarish allaqachon qayd etilgan.") from None
        raise


def grant_excused_credit(charge: LedgerEntry, user, comment: str) -> LedgerEntry:
    """
    Sababli kelmagan (excused) uchun to'lovni qaytaradi (excused_credit).

    Huquqlar: administrator va director.
    Bir charge uchun faqat bitta excused_credit yaratish mumkin.
    comment majburiy.
    """
    if not comment or not comment.strip():
        raise ValidationError("Sababli qaytarish uchun izoh majburiy.")

    if charge.kind != LedgerEntry.Kind.CHARGE:
        raise ValidationError("Faqat charge turidagi yozuvga excused_credit berish mumkin.")

    if user is not None and not user_has_role(user, DIRECTOR_ONLY):
        raise PermissionDenied("Faqat direktor excused_credit berishi mumkin.")

    # Tekshirish: allaqachon mavjudmi?
    existing = LedgerEntry.objects.filter(
        kind=LedgerEntry.Kind.EXCUSED_CREDIT,
        reverses=charge,
    ).first()
    if existing:
        raise ValidationError("Ushbu charge uchun allaqachon excused_credit mavjud.")

    try:
        with transaction.atomic():
            return LedgerEntry.objects.create(
                student=charge.student,
                group=charge.group,
                enrollment=charge.enrollment,
                lesson=charge.lesson,
                kind=LedgerEntry.Kind.EXCUSED_CREDIT,
                amount=-charge.amount,  # charge manfiy, excused_credit musbat
                entry_date=charge.entry_date,
                comment=comment,
                created_by=user,
                reverses=charge,
            )
    except IntegrityError:
        # Ikki so'rov bir vaqtda: ikkinchisini unique_excused_credit_per_charge to'xtatdi
        raise ValidationError("Ushbu charge uchun allaqachon excused_credit mavjud.") from None


def add_adjustment(
    student,
    group,
    amount: int,
    user,
    comment: str,
) -> LedgerEntry:
    """
    Qo'lda moliyaviy tuzatish qo'shadi.

    Huquqlar: faqat director.
    comment majburiy.
    """
    if not comment or not comment.strip():
        raise ValidationError("Tuzatish uchun izoh majburiy.")

    if user is not None and not user_has_role(user, DIRECTOR_ONLY):
        raise PermissionDenied("Faqat direktor tuzatish qo'shishi mumkin.")

    from django.utils import timezone

    with transaction.atomic():
        entry = LedgerEntry.objects.create(
            student=student,
            group=group,
            kind=LedgerEntry.Kind.ADJUSTMENT,
            amount=amount,
            entry_date=timezone.localdate(),
            comment=comment,
            created_by=user,
        )
        return entry


def get_balance(student, group=None) -> int:
    """
    O'quvchining balansini hisoblaydi.

    Manfiy balans = qarz.
    Agar group ko'rsatilsa, faqat o'sha guruh uchun; aks holda barcha guruhlar.
    """
    qs = LedgerEntry.objects.filter(student=student)
    if group:
        qs = qs.filter(group=group)

    result = qs.aggregate(total=Sum("amount"))
    return result["total"] or 0


def get_debtors(group=None, search="") -> list[dict]:
    """
    Qarzdor o'quvchilarni ro'yxatini qaytaradi.

    Har bir element: {student, group, balance} (balance < 0).
    N+1 muammosini oldini olish uchun agregatsiya qilinadi.

    ``group`` (obyekt yoki pk) va ``search`` (ism/familiya/telefon) filtrlari
    ma'lumotlar bazasida qo'llanadi, shuning uchun jami summa ham filtrga mos keladi.
    """
    qs = LedgerEntry.objects.all()
    if group:
        qs = qs.filter(group=group)
    if search and search.strip():
        qs = qs.filter(
            student__in=Student.objects.filter(person_search_q(search)).values("pk")
        )

    # Guruh va student bo'yicha guruhlash
    aggregated = (
        qs.values("student", "group")
        .annotate(balance=Sum("amount"))
        .filter(balance__lt=0)
        .order_by("balance")
    )

    # Student va Group ob'ektlarini olish
    from apps.groups.models import Group

    student_ids = [item["student"] for item in aggregated]
    group_ids = [item["group"] for item in aggregated]

    students_map = {s.pk: s for s in Student.objects.filter(pk__in=student_ids)}
    groups_map = {g.pk: g for g in Group.objects.filter(pk__in=group_ids)}

    result = []
    for item in aggregated:
        result.append(
            {
                "student": students_map.get(item["student"]),
                "group": groups_map.get(item["group"]),
                "balance": item["balance"],
            }
        )

    return result



def compute_payroll(year: int, month: int) -> dict:
    """
    Ish haqi hisobini hisoblaydi (yopilgan yoki ochiq oy uchun).

    Yopilgan oy uchun: saqlangan PayrollLine'larni qaytaradi.
    Ochiq oy uchun: "jonli" hisoblaydi (to'lovlar, foizlar, tuzatishlar).

    Qaytaradi: {teacher_id: {teacher, pay_type, groups: [{group, base, percent, amount}],
                             fixed_salary, adjustments, total_accrued, total_paid, balance}}
    """
    from calendar import monthrange

    from django.db.models import Q, Sum

    from apps.finance.models import (
        PayrollAdjustment,
        PayrollLine,
        PayrollMonth,
        SalaryPayout,
    )
    from apps.groups.models import Group
    from apps.teachers.models import Teacher

    # Проверяем, закрыт ли месяц
    try:
        payroll_month = PayrollMonth.objects.get(year=year, month=month)
        is_closed = payroll_month.is_closed
    except PayrollMonth.DoesNotExist:
        payroll_month = None
        is_closed = False

    if is_closed:
        # Возвращаем сохранённые данные
        lines = (
            PayrollLine.objects.filter(month=payroll_month)
            .select_related("teacher", "group")
            .order_by("teacher__full_name", "group__name")
        )

        result = {}
        for line in lines:
            teacher_id = line.teacher.pk
            if teacher_id not in result:
                result[teacher_id] = {
                    "teacher": line.teacher,
                    "pay_type": line.kind,
                    "groups": [],
                    "fixed_salary": None,
                    "adjustments": 0,
                    "total_accrued": 0,
                    "total_paid": 0,
                    "balance": 0,
                    "missing_percent": [],
                }

            if line.kind == PayrollLine.Kind.PERCENT:
                # Yopilgan oy: foiz yopish paytida muzlatilgan (keyingi o'zgarishlar ta'sir qilmaydi)
                result[teacher_id]["groups"].append(
                    {
                        "group": line.group,
                        "base": line.base_amount,
                        "percent": line.percent,
                        "source": "closed",
                        "amount": line.amount,
                        "missing": False,
                    }
                )
            else:  # FIXED
                result[teacher_id]["fixed_salary"] = line.amount

            result[teacher_id]["total_accrued"] += line.amount

        # Добавляем adjustments и payouts для закрытого месяца
        adjustments = PayrollAdjustment.objects.filter(
            month=payroll_month
        ).select_related("teacher")
        for adj in adjustments:
            tid = adj.teacher.pk
            if tid in result:
                result[tid]["adjustments"] += adj.amount
                result[tid]["total_accrued"] += adj.amount

        payouts = SalaryPayout.objects.filter(month=payroll_month).select_related(
            "teacher"
        )
        for payout in payouts:
            tid = payout.teacher.pk
            if tid in result:
                result[tid]["total_paid"] += payout.amount

        for tid in result:
            result[tid]["balance"] = (
                result[tid]["total_accrued"] - result[tid]["total_paid"]
            )

        return result

    # Открытый месяц: считаем "вживую"
    _, last_day = monthrange(year, month)
    start_date = date(year, month, 1)
    end_date = date(year, month, last_day)

    # Faol o'qituvchilar + faol bo'lmagan, lekin shu oyda hisoblanadigan summasi bor o'qituvchilar
    # (ishdan chiqarilgan o'qituvchining qarzi yo'qolib qolmasin)
    involved = set(
        LedgerEntry.objects.filter(
            kind__in=INCOME_KINDS, entry_date__gte=start_date, entry_date__lte=end_date
        ).values_list("group__teacher", flat=True)
    )
    if payroll_month:
        involved |= set(PayrollAdjustment.objects.filter(month=payroll_month).values_list("teacher", flat=True))
        involved |= set(SalaryPayout.objects.filter(month=payroll_month).values_list("teacher", flat=True))
    teachers = list(Teacher.objects.filter(Q(is_active=True) | Q(pk__in=involved)))

    # Barcha hisob-kitob uchun ma'lumotlar bir nechta guruhlangan so'rovda olinadi (o'qituvchi/guruhga alohida so'rov yo'q)
    groups_by_teacher = defaultdict(list)
    for group in Group.objects.filter(teacher__in=teachers):
        groups_by_teacher[group.teacher_id].append(group)
    payments_by_group = {
        row["group"]: row["total"] or 0
        for row in LedgerEntry.objects.filter(
            kind__in=INCOME_KINDS,
            group__teacher__in=teachers,
            entry_date__gte=start_date,
            entry_date__lte=end_date,
        )
        .values("group")
        .annotate(total=Sum("amount"))
    }
    adjustments_by_teacher = {}
    payouts_by_teacher = {}
    if payroll_month:
        adjustments_by_teacher = {
            row["teacher"]: row["total"] or 0
            for row in PayrollAdjustment.objects.filter(month=payroll_month).values("teacher").annotate(total=Sum("amount"))
        }
        payouts_by_teacher = {
            row["teacher"]: row["total"] or 0
            for row in SalaryPayout.objects.filter(month=payroll_month).values("teacher").annotate(total=Sum("amount"))
        }

    result = {}

    for teacher in teachers:
        teacher_id = teacher.pk
        result[teacher_id] = {
            "teacher": teacher,
            "pay_type": teacher.pay_type,
            "groups": [],
            "fixed_salary": None,
            "adjustments": 0,
            "total_accrued": 0,
            "total_paid": 0,
            "balance": 0,
            "missing_percent": [],  # foizi sozlanmagan guruhlar: ish haqi hisoblanmaydi
        }

        if teacher.pay_type == Teacher.PayType.PERCENT:
            # Har bir guruh uchun baza — shu oydagi to'lovlar yig'indisi (LedgerEntry kind=payment)
            for group in groups_by_teacher[teacher_id]:
                payments_sum = payments_by_group.get(group.pk, 0)
                percent, source = group.resolve_teacher_percent(teacher)
                if percent is None:
                    # Foiz na guruhda, na o'qituvchi profilida yo'q: o'ylab topilgan stavka bilan hisoblanmaydi
                    if payments_sum > 0 or group.status == Group.Status.ACTIVE:
                        row = {"group": group, "base": payments_sum, "percent": None, "source": None, "amount": 0, "missing": True}
                        result[teacher_id]["groups"].append(row)
                        result[teacher_id]["missing_percent"].append(row)
                    continue
                if payments_sum > 0 or percent > 0:
                    accrued = payments_sum * percent // 100
                    result[teacher_id]["groups"].append(
                        {
                            "group": group,
                            "base": payments_sum,
                            "percent": percent,
                            "source": source,
                            "amount": accrued,
                            "missing": False,
                        }
                    )
                    result[teacher_id]["total_accrued"] += accrued

        else:  # FIXED
            if teacher.monthly_salary:
                result[teacher_id]["fixed_salary"] = teacher.monthly_salary
                result[teacher_id]["total_accrued"] = teacher.monthly_salary

        if payroll_month:
            adjustments_sum = adjustments_by_teacher.get(teacher_id, 0)
            result[teacher_id]["adjustments"] = adjustments_sum
            result[teacher_id]["total_accrued"] += adjustments_sum
            result[teacher_id]["total_paid"] = payouts_by_teacher.get(teacher_id, 0)

        result[teacher_id]["balance"] = (
            result[teacher_id]["total_accrued"] - result[teacher_id]["total_paid"]
        )

    return result


def close_payroll_month(year: int, month: int, user) -> "PayrollMonth":
    """
    Oylik hisobni yopadi (faqat director).

    Qoidalar:
    - Faqat director.
    - Oy tugashi kerak (oxirgi kun bugundan oldin).
    - Takroriy yopish taqiqlangan.
    - PayrollLine'larni saqlaydi.
    """
    from calendar import monthrange

    from django.utils import timezone

    from apps.finance.models import PayrollLine, PayrollMonth
    from apps.teachers.models import Teacher

    if user is not None and not user_has_role(user, DIRECTOR_ONLY):
        raise PermissionDenied("Faqat direktor oyni yopishi mumkin.")

    # Проверка: месяц уже завершён
    _, last_day = monthrange(year, month)
    month_end = date(year, month, last_day)
    today = timezone.localdate()

    if month_end >= today:
        raise ValidationError(
            f"Oy {year}-{month:02d} hali tugamagan. Oyni yopish uchun oxirgi kun ({month_end}) o'tishi kerak."
        )

    with transaction.atomic():
        # Oyni qulflaymiz: bir vaqtda ikkita yopilishning oldini olish uchun
        payroll_month, _created = PayrollMonth.objects.get_or_create(
            year=year, month=month, defaults={"closed_at": None, "closed_by": None}
        )
        payroll_month = PayrollMonth._base_manager.select_for_update().get(
            pk=payroll_month.pk
        )

        # Проверка: уже закрыт? (читаем из БД, под блокировкой)
        if payroll_month.is_closed:
            raise ValidationError(f"Oy {year}-{month:02d} allaqachon yopilgan.")

        # Вычисляем payroll
        payroll_data = compute_payroll(year, month)

        # Foizi sozlanmagan guruhda shu oyda to'lov bo'lsa, oyni yopib bo'lmaydi: aks holda
        # o'qituvchining ulushi 0 bo'lib muzlab qoladi
        unpriced = [
            f"{row['group'].name} ({data['teacher'].full_name})"
            for data in payroll_data.values()
            for row in data.get("missing_percent", [])
            if row["base"] > 0
        ]
        if unpriced:
            raise ValidationError(
                "Ish haqi foizi sozlanmagan guruhlar bor: " + ", ".join(unpriced)
                + ". Guruhda yoki o'qituvchi profilida foizni belgilang, keyin oyni yoping."
            )

        # Сохраняем PayrollLine для каждого преподавателя
        for _teacher_id, data in payroll_data.items():
            teacher = data["teacher"]

            if data["pay_type"] == Teacher.PayType.PERCENT:
                for group_data in data["groups"]:
                    if group_data.get("missing"):
                        continue  # to'lovsiz va foizsiz guruh: yoziladigan narsa yo'q
                    PayrollLine.objects.create(
                        month=payroll_month,
                        teacher=teacher,
                        group=group_data["group"],
                        kind=PayrollLine.Kind.PERCENT,
                        base_amount=group_data["base"],
                        percent=group_data["percent"],
                        amount=group_data["amount"],
                    )
            else:  # FIXED
                if data["fixed_salary"]:
                    PayrollLine.objects.create(
                        month=payroll_month,
                        teacher=teacher,
                        group=None,
                        kind=PayrollLine.Kind.FIXED,
                        base_amount=data["fixed_salary"],
                        percent=None,
                        amount=data["fixed_salary"],
                    )

        # Закрываем месяц
        payroll_month.closed_at = timezone.now()
        payroll_month.closed_by = user
        payroll_month.save(update_fields=["closed_at", "closed_by"])

    return payroll_month


def add_payroll_adjustment(
    year: int, month: int, teacher, amount: int, comment: str, user
) -> "PayrollAdjustment":
    """
    Oylik ish haqi tuzatishini qo'shadi.

    Huquqlar: administrator va director.
    Oy yopilmagan bo'lishi kerak.
    comment majburiy.
    """
    from apps.finance.models import PayrollAdjustment, PayrollMonth

    if not comment or not comment.strip():
        raise ValidationError("Tuzatish uchun izoh majburiy.")

    if user is not None and not user_has_role(user, DIRECTOR_ONLY):
        raise PermissionDenied("Faqat direktor ish haqi tuzatishini qo'shishi mumkin.")

    # Проверка: месяц не закрыт
    payroll_month, created = PayrollMonth.objects.get_or_create(
        year=year, month=month, defaults={"closed_at": None, "closed_by": None}
    )

    if payroll_month.is_closed:
        raise ValidationError(
            f"Oy {year}-{month:02d} yopilgan. Yopilgan oyga tuzatish qo'shib bo'lmaydi."
        )

    with transaction.atomic():
        adjustment = PayrollAdjustment.objects.create(
            month=payroll_month,
            teacher=teacher,
            amount=amount,
            comment=comment,
            created_by=user,
        )
        return adjustment


def register_salary_payout(
    teacher, year: int, month: int, amount: int, method: str, paid_date: date, comment: str, user
) -> "SalaryPayout":
    """
    Ish haqi to'lovini ro'yxatga oladi.

    Huquqlar: administrator va director.
    amount musbat bo'lishi kerak.
    """
    from apps.finance.models import PayrollMonth, SalaryPayout

    if amount <= 0:
        raise ValidationError("To'lov summasi musbat bo'lishi kerak.")

    if user is not None and not user_has_role(user, DIRECTOR_ONLY):
        raise PermissionDenied("Faqat direktor ish haqi to'lovini ro'yxatga olishi mumkin.")

    if method not in dict(LedgerEntry.PaymentMethod.choices):
        raise ValidationError(f"Noto'g'ri to'lov usuli: {method}")

    # Получаем или создаём PayrollMonth
    payroll_month, created = PayrollMonth.objects.get_or_create(
        year=year, month=month, defaults={"closed_at": None, "closed_by": None}
    )

    with transaction.atomic():
        payout = SalaryPayout.objects.create(
            teacher=teacher,
            month=payroll_month,
            amount=amount,
            method=method,
            paid_date=paid_date,
            comment=comment,
            created_by=user,
        )
        return payout


def generate_receipt_qr_svg(content: str) -> str:
    """Генерирует чистый SVG QR-код для чека."""
    if not content:
        return ""
    try:
        import qrcode
        import qrcode.image.svg

        factory = qrcode.image.svg.SvgPathImage
        img = qrcode.make(content, image_factory=factory, box_size=10, border=1)
        svg_xml = img.to_string(encoding="unicode")
        if "<?xml" in svg_xml:
            svg_xml = svg_xml.split("?>", 1)[-1].strip()
        return svg_xml
    except ImportError:
        logger.warning("qrcode paketi o'rnatilmagan: chekda QR kod ko'rsatilmaydi")
        return ""
    except (ValueError, OSError):
        logger.warning("QR kodni yaratib bo'lmadi (manzil juda uzun?)", exc_info=True)
        return ""
