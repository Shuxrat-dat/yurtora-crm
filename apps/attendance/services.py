"""Business logic for attendance marking with log tracking."""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.attendance.models import Attendance, AttendanceLog
from apps.groups.models import Enrollment
from apps.schedule.models import Lesson
from apps.students.models import Student


@transaction.atomic
def mark_attendance(lesson: Lesson, student: Student, status: str, user) -> Attendance:
    """
    O'quvchining dars davomatini belgilaydi.

    Qoidalar:
    - Bekor qilingan (cancelled) darsda davomat belgilab bo'lmaydi.
    - Faqat shu guruhda sana holatida faol a'zolik (Enrollment) mavjud o'quvchilarni
      belgilash mumkin.
    - Har bir holat o'zgarishi AttendanceLog ga yoziladi.
    """
    if lesson.status == Lesson.Status.CANCELLED:
        raise ValidationError("Bekor qilingan darsda davomat belgilab bo'lmaydi.")

    # O'quvchi shu guruhda dars sanasida faol a'zo ekanini tekshirish
    is_enrolled = Enrollment.objects.filter(
        student=student,
        group=lesson.group,
        # Sinov darsidagi o'quvchi ham darsga keladi: davomati belgilanadi (to'lov hisoblanmaydi)
        status__in=[Enrollment.Status.ACTIVE, Enrollment.Status.TRIAL],
        start_date__lte=lesson.date,
    ).filter(
        # end_date yo'q yoki dars sanasidan keyin
        models_q_end_date_ok(lesson.date)
    ).exists()

    if not is_enrolled:
        raise ValidationError(
            f"'{student.full_name}' o'quvchisi {lesson.date} sanasida "
            f"'{lesson.group.name}' guruhida faol a'zo emas."
        )

    if status not in [s.value for s in Attendance.Status]:
        raise ValidationError(f"Noto'g'ri davomat holati: '{status}'.")

    attendance, created = Attendance.objects.get_or_create(
        lesson=lesson,
        student=student,
        defaults={"status": status, "marked_by": user},
    )

    old_status = "" if created else attendance.status

    if not created:
        if attendance.status == status:
            return attendance  # Hech narsa o'zgarmadi
        attendance.status = status
        attendance.marked_by = user
        attendance.save(update_fields=["status", "marked_by", "updated_at"])

    # Jurnal yozuvi
    AttendanceLog.objects.create(
        attendance=attendance,
        old_status=old_status,
        new_status=status,
        changed_by=user,
        changed_at=timezone.now(),
    )

    # Davomat belgilangan bo'lsa, dars o'tgan: "o'tildi" deb belgilanadi va to'lovlar hisoblanadi.
    # Huquqlar chaqiruvchi view'da tekshirilgan (o'rinbosar o'qituvchi ham belgilay olishi uchun user=None).
    attendance.lesson_marked_held = False
    if lesson.status == Lesson.Status.SCHEDULED and lesson.date <= timezone.localdate():
        from apps.schedule.services import mark_lesson_held

        mark_lesson_held(lesson, None)
        attendance.lesson_marked_held = True

    return attendance


def models_q_end_date_ok(lesson_date):
    """end_date bo'sh (null) yoki dars sanasidan katta/teng bo'lgan shartni qaytaradi."""
    from django.db.models import Q
    return Q(end_date__isnull=True) | Q(end_date__gte=lesson_date)


def get_attendance_stats(student: Student) -> dict:
    """
    O'quvchining umumiy davomat statistikasini hisoblaydi.
    Faqat 'held' (o'tilgan) darslar hisobga olinadi.
    """
    from apps.attendance.models import Attendance

    qs = Attendance.objects.filter(
        student=student,
        lesson__status=Lesson.Status.HELD,
    )
    total = qs.count()
    present = qs.filter(status=Attendance.Status.PRESENT).count()
    late = qs.filter(status=Attendance.Status.LATE).count()
    absent = qs.filter(status=Attendance.Status.ABSENT).count()
    excused = qs.filter(status=Attendance.Status.EXCUSED).count()

    attended = present + late
    percent = round(attended / total * 100) if total else 0

    return {
        "total": total,
        "present": present,
        "late": late,
        "absent": absent,
        "excused": excused,
        "attended": attended,
        "percent": percent,
    }
