"""Business logic services for group management and student enrollment."""

from datetime import date, timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.finance.services import backfill_enrollment_charges
from apps.groups.models import Enrollment, EnrollmentLog, Group
from apps.students.models import Student


def enroll_student(
    student: Student,
    group: Group,
    start_date: date | None = None,
    status: str = Enrollment.Status.ACTIVE,
    user=None,
    paid_from: date | None = None,
) -> Enrollment:
    """
    O'quvchini guruhga a'zo (faol yoki sinov darsi) sifatida qo'shish xizmati.

    Qoidalar:
    - Yopiq guruhga o'quvchi qo'shib bo'lmaydi.
    - Faqat 'active' yoki 'trial' statusi bilan qo'shish mumkin.
    - Guruhdagi umumiy o'quvchilar (faol + sinov + muzlatilgan) soni max_students dan oshib ketishi taqiqlanadi.
    - Bitta guruhda o'quvchining bir vaqtda ikkita tugallanmagan (faol/sinov/muzlatilgan) a'zoligi bo'lishi mumkin emas.
    - Sinov darsi bepul (paid_from=None). Faol a'zolikda paid_from boshlanish sanasiga teng bo'ladi.
    """
    if start_date is None:
        start_date = timezone.localdate()

    if status not in [Enrollment.Status.ACTIVE, Enrollment.Status.TRIAL]:
        raise ValidationError("Noto'g'ri a'zolik statusi tanlandi.")

    if status == Enrollment.Status.TRIAL:
        paid_from = None
    elif paid_from is None:
        paid_from = start_date

    with transaction.atomic():
        # Poyga holatini (race condition) oldini olish uchun guruh qatorini qulflaymiz
        locked_group = Group.objects.select_for_update().get(pk=group.pk)

        if locked_group.status != Group.Status.ACTIVE:
            raise ValidationError("Yopilgan guruhga o'quvchi qo'shish mumkin emas.")

        # O'quvchi allaqachon ushbu guruhda ochiq a'zolikka ega ekanligini tekshirish
        is_already_enrolled = Enrollment.objects.filter(
            student=student,
            group=locked_group,
            status__in=[
                Enrollment.Status.ACTIVE,
                Enrollment.Status.TRIAL,
                Enrollment.Status.FROZEN,
            ],
        ).exists()

        if is_already_enrolled:
            raise ValidationError(
                "O'quvchi ushbu guruhda allaqachon faol a'zo yoki sinov darsida mavjud."
            )

        # Maksimal o'quvchilar soni tekshiruvi (sinov va muzlatilganlar ham hisobga olinadi)
        current_count = Enrollment.objects.filter(
            group=locked_group,
            status__in=[
                Enrollment.Status.ACTIVE,
                Enrollment.Status.TRIAL,
                Enrollment.Status.FROZEN,
            ],
        ).count()

        if current_count >= locked_group.max_students:
            raise ValidationError(
                f"Guruh to'lgan. Maksimal o'quvchilar soni: {locked_group.max_students} nafar."
            )

        enrollment = Enrollment.objects.create(
            student=student,
            group=locked_group,
            start_date=start_date,
            status=status,
            paid_from=paid_from,
        )

        EnrollmentLog.objects.create(
            enrollment=enrollment,
            action="enroll",
            old_status=None,
            new_status=status,
            changed_by=user,
            note="Guruhga qo'shildi",
        )

        # O'tmish sanadan yozilgan bo'lsa, allaqachon o'tilgan darslar ham hisoblanadi
        enrollment.backfilled_charges = backfill_enrollment_charges(enrollment)
        return enrollment


def remove_student(
    student: Student,
    group: Group,
    end_date: date | None = None,
    user=None,
    note: str = "",
) -> Enrollment:
    """
    O'quvchini guruhdan chiqarish (a'zolikni tugatish) xizmati.
    """
    if end_date is None:
        end_date = timezone.localdate()

    with transaction.atomic():
        enrollment = (
            Enrollment.objects.select_for_update()
            .filter(
                student=student,
                group=group,
                status__in=[
                    Enrollment.Status.ACTIVE,
                    Enrollment.Status.TRIAL,
                    Enrollment.Status.FROZEN,
                ],
            )
            .first()
        )

        if not enrollment:
            raise ValidationError(
                "O'quvchining ushbu guruhda faol, sinov yoki muzlatilgan a'zoligi topilmadi."
            )

        old_status = enrollment.status
        enrollment.status = Enrollment.Status.FINISHED
        enrollment.end_date = end_date
        enrollment.save(update_fields=["status", "end_date", "updated_at"])

        EnrollmentLog.objects.create(
            enrollment=enrollment,
            action="remove",
            old_status=old_status,
            new_status=Enrollment.Status.FINISHED,
            changed_by=user,
            note=note or "Guruhdan chiqarildi",
        )

        return enrollment


def transfer_student(
    student: Student,
    from_group: Group,
    to_group: Group,
    user=None,
    start_date: date | None = None,
    carry_balance: bool = False,
    note: str = "",
) -> Enrollment:
    """
    O'quvchini bir guruhdan boshqasiga ko'chirish (bitta tranzaksiyada).

    Eski a'zolik start_date dan oldingi kunda tugaydi, yangisi start_date dan boshlanadi
    (sinov holati saqlanadi: sinovdagi o'quvchi yangi guruhda ham sinovda). Qarz/ortiqcha to'lov
    o'zgarishsiz eski guruhda qoladi, agar carry_balance=True bo'lmasa. carry_balance faqat direktor uchun:
    eski guruh balansi nolga tushadi, yangi guruhga shu summa o'tadi (ikkita tuzatish yozuvi).
    """
    from apps.accounts.models import User
    from apps.accounts.permissions import user_has_role
    from apps.finance.models import LedgerEntry
    from apps.finance.services import get_balance

    if from_group.pk == to_group.pk:
        raise ValidationError("Boshqa guruhni tanlang.")
    if carry_balance and user is not None and not user_has_role(user, [User.Role.DIRECTOR]):
        raise PermissionDenied("Balansni ko'chirish faqat direktor uchun.")

    if start_date is None:
        start_date = timezone.localdate()

    with transaction.atomic():
        old = (
            Enrollment.objects.select_for_update()
            .filter(
                student=student,
                group=from_group,
                status__in=[Enrollment.Status.ACTIVE, Enrollment.Status.TRIAL, Enrollment.Status.FROZEN],
            )
            .first()
        )
        if not old:
            raise ValidationError("O'quvchining eski guruhda ochiq a'zoligi topilmadi.")

        new_status = Enrollment.Status.TRIAL if old.status == Enrollment.Status.TRIAL else Enrollment.Status.ACTIVE
        new = enroll_student(student, to_group, start_date=start_date, status=new_status, user=user)
        end_date = max(start_date - timedelta(days=1), old.start_date)
        remove_student(
            student,
            from_group,
            end_date=end_date,
            user=user,
            note=note or f"Boshqa guruhga ko'chirildi: {to_group.name}",
        )
        EnrollmentLog.objects.create(
            enrollment=new,
            action="transfer",
            old_status=old.status,
            new_status=new.status,
            changed_by=user,
            note=note or f"{from_group.name} guruhidan ko'chirildi",
        )

        if carry_balance:
            balance = get_balance(student, from_group)
            if balance:
                today = timezone.localdate()
                comment = f"Guruhlar orasida ko'chirish: {from_group.name} → {to_group.name}"
                LedgerEntry.objects.create(
                    student=student, group=from_group, kind=LedgerEntry.Kind.ADJUSTMENT,
                    amount=-balance, entry_date=today, comment=comment, created_by=user,
                )
                LedgerEntry.objects.create(
                    student=student, group=to_group, kind=LedgerEntry.Kind.ADJUSTMENT,
                    amount=balance, entry_date=today, comment=comment, created_by=user,
                )
        return new


def convert_trial_to_active(
    enrollment: Enrollment,
    paid_from: date | None = None,
    user=None,
    note: str = "",
) -> Enrollment:
    """
    Sinov darsidagi o'quvchini (trial) faol a'zolikka (active) o'tkazish xizmati.

    Qoidalar:
    - Huquqlar: faqat administrator va director.
    - Faqat 'trial' statusidagi a'zolikni 'active' ga o'tkazish mumkin.
    - Guruh faol (active) holatda bo'lishi shart.
    - paid_from: ko'rsatilmagan bo'lsa, joriy sana olinadi.
    - EnrollmentLog yoziladi.
    """
    if user is not None:
        is_admin_or_director = (
            getattr(user, "is_director", False)
            or getattr(user, "is_administrator", False)
            or getattr(user, "is_superuser", False)
        )
        if not is_admin_or_director:
            raise PermissionDenied(
                "Faqat direktor va administrator sinov darsini faolga o'tkazishi mumkin."
            )

    with transaction.atomic():
        locked_enrollment = (
            Enrollment.objects.select_for_update()
            .select_related("group", "student")
            .filter(pk=enrollment.pk)
            .first()
        )

        if not locked_enrollment:
            raise ValidationError("A'zolik yozuvi topilmadi.")

        if locked_enrollment.status != Enrollment.Status.TRIAL:
            raise ValidationError(
                "Faqat sinov darsidagi (trial) a'zolikni faol holatga o'tkazish mumkin."
            )

        if locked_enrollment.group.status != Group.Status.ACTIVE:
            raise ValidationError(
                "Yopilgan guruhdagi a'zolikni faol holatga o'tkazib bo'lmaydi."
            )

        if paid_from is None:
            paid_from = timezone.localdate()

        old_status = locked_enrollment.status
        locked_enrollment.status = Enrollment.Status.ACTIVE
        locked_enrollment.paid_from = paid_from
        locked_enrollment.save(update_fields=["status", "paid_from", "updated_at"])

        EnrollmentLog.objects.create(
            enrollment=locked_enrollment,
            action="convert_trial",
            old_status=old_status,
            new_status=Enrollment.Status.ACTIVE,
            changed_by=user,
            note=note or "Sinov darsi faol holatga o'tkazildi",
        )

        locked_enrollment.backfilled_charges = backfill_enrollment_charges(locked_enrollment)
        return locked_enrollment


def freeze_enrollment(
    enrollment: Enrollment,
    user=None,
    note: str = "",
) -> Enrollment:
    """
    Faol a'zolikni muzlatish xizmati.

    Qoidalar:
    - Huquqlar: teacher (faqat o'z guruhlari), administrator, director.
    - Faqat 'active' statusidagi a'zolikni muzlatish mumkin.
    - frozen_from sanasi belgilanadi.
    - Guruhda o'rin band qilishda davom etadi.
    - EnrollmentLog yoziladi.
    """
    if user is not None:
        is_admin_or_director = (
            getattr(user, "is_director", False)
            or getattr(user, "is_administrator", False)
            or getattr(user, "is_superuser", False)
        )
        is_teacher = getattr(user, "is_teacher", False) or (
            getattr(user, "role", "") == "teacher"
        )
        if is_teacher:
            group_teacher_user = (
                enrollment.group.teacher.user
                if enrollment.group and enrollment.group.teacher
                else None
            )
            if group_teacher_user != user:
                raise PermissionDenied(
                    "O'qituvchi faqat o'z guruhidagi o'quvchilarni muzlatishi mumkin."
                )
        elif not is_admin_or_director:
            raise PermissionDenied("Ushbu amalni bajarish uchun ruxsat yo'q.")

    with transaction.atomic():
        locked_enrollment = (
            Enrollment.objects.select_for_update()
            .select_related("group", "student", "group__teacher")
            .filter(pk=enrollment.pk)
            .first()
        )

        if not locked_enrollment:
            raise ValidationError("A'zolik yozuvi topilmadi.")

        if locked_enrollment.status != Enrollment.Status.ACTIVE:
            raise ValidationError("Faqat faol a'zolikni muzlatish mumkin.")

        old_status = locked_enrollment.status
        locked_enrollment.status = Enrollment.Status.FROZEN
        locked_enrollment.frozen_from = timezone.localdate()
        locked_enrollment.save(update_fields=["status", "frozen_from", "updated_at"])

        EnrollmentLog.objects.create(
            enrollment=locked_enrollment,
            action="freeze",
            old_status=old_status,
            new_status=Enrollment.Status.FROZEN,
            changed_by=user,
            note=note or "A'zolik muzlatildi",
        )

        return locked_enrollment


def unfreeze_enrollment(
    enrollment: Enrollment,
    user=None,
    date: date | None = None,
    note: str = "",
) -> Enrollment:
    """
    Muzlatilgan a'zolikni qayta faollashtirish (muzlatishni bekor qilish) xizmati.

    Qoidalar:
    - Huquqlar: faqat administrator va director (teacher EMAS).
    - Faqat 'frozen' statusidagi a'zolikni faollashtirish mumkin.
    - frozen_from tozalanadi.
    - Agar sana (date) berilsa, to'lov shu sanadan boshlab hisoblanadi (paid_from).
    - EnrollmentLog yoziladi.
    """
    if user is not None:
        is_admin_or_director = (
            getattr(user, "is_director", False)
            or getattr(user, "is_administrator", False)
            or getattr(user, "is_superuser", False)
        )
        if not is_admin_or_director:
            raise PermissionDenied(
                "Faqat administrator va direktor muzlatishni bekor qilishi mumkin."
            )

    with transaction.atomic():
        locked_enrollment = (
            Enrollment.objects.select_for_update()
            .select_related("group", "student", "group__teacher")
            .filter(pk=enrollment.pk)
            .first()
        )

        if not locked_enrollment:
            raise ValidationError("A'zolik yozuvi topilmadi.")

        if locked_enrollment.status != Enrollment.Status.FROZEN:
            raise ValidationError(
                "Faqat muzlatilgan a'zolikni muzlatishdan chiqarish mumkin."
            )

        old_status = locked_enrollment.status
        locked_enrollment.status = Enrollment.Status.ACTIVE
        locked_enrollment.frozen_from = None
        update_fields = ["status", "frozen_from", "updated_at"]

        if date is not None:
            locked_enrollment.paid_from = date
            update_fields.append("paid_from")

        locked_enrollment.save(update_fields=update_fields)

        EnrollmentLog.objects.create(
            enrollment=locked_enrollment,
            action="unfreeze",
            old_status=old_status,
            new_status=Enrollment.Status.ACTIVE,
            changed_by=user,
            note=note or "A'zolik muzlatishdan chiqarildi",
        )

        return locked_enrollment


OPEN_STATUSES = [Enrollment.Status.ACTIVE, Enrollment.Status.TRIAL, Enrollment.Status.FROZEN]


def finish_open_enrollments(student=None, group=None, user=None, note="") -> int:
    """Ochiq a'zoliklarni (faol/sinov/muzlatilgan) tugatadi: o'quvchi arxivlanganda yoki guruh yopilganda.

    Shundan keyin hisoblash (accrue_lesson_charges) to'xtaydi. Tugatilgan a'zoliklar sonini qaytaradi.
    """
    qs = Enrollment.objects.filter(status__in=OPEN_STATUSES).select_related("student", "group")
    if student is not None:
        qs = qs.filter(student=student)
    if group is not None:
        qs = qs.filter(group=group)
    finished = 0
    with transaction.atomic():
        for enrollment in list(qs):
            remove_student(enrollment.student, enrollment.group, user=user, note=note)
            finished += 1
    return finished


def close_group(group: Group, user=None) -> dict:
    """Guruhni yopishning oqibatlari: a'zoliklar tugatiladi, kelajakdagi rejalashtirilgan darslar bekor qilinadi."""
    from apps.schedule.models import Lesson
    from apps.schedule.services import cancel_lesson

    today = timezone.localdate()
    with transaction.atomic():
        finished = finish_open_enrollments(group=group, user=user, note="Guruh yopildi")
        cancelled = 0
        for lesson in Lesson.objects.filter(group=group, status=Lesson.Status.SCHEDULED, date__gte=today):
            cancel_lesson(lesson, reason="Guruh yopildi")
            cancelled += 1
    return {"enrollments": finished, "lessons": cancelled}
