"""O'quvchi kartasi uchun ma'lumotlar: guruhlar bo'yicha oylik davomat taqvimi va o'zgarishlar tarixi."""

from collections import Counter
from datetime import timedelta

from apps.attendance.models import Attendance
from apps.groups.models import EnrollmentLog
from apps.schedule.models import Lesson

STATE_LABELS = {
    "present": "Keldi",
    "late": "Kechikdi",
    "absent": "Kelmadi",
    "excused": "Sababli",
    "unmarked": "Belgilanmagan",
    "upcoming": "Kutilmoqda",
    "cancelled": "Bekor qilingan",
}

ENROLLMENT_ACTIONS = {
    "enroll": "Guruhga qo'shildi",
    "convert_trial": "Sinovdan faol a'zolikka o'tdi",
    "freeze": "Muzlatildi",
    "unfreeze": "Muzlatish bekor qilindi",
    "remove": "Guruhdan chiqarildi",
}

STATUS_LABELS = {
    "active": "Faol",
    "trial": "Sinov",
    "frozen": "Muzlatilgan",
    "finished": "Tugallangan",
}


def month_bounds(month_start):
    next_month = (month_start + timedelta(days=32)).replace(day=1)
    return month_start, next_month


def attendance_calendar(student, enrollment, month_start, today):
    """Shu oydagi guruh darslari va o'quvchining har bir darsdagi holati.

    A'zolik boshlanishidan oldingi va tugaganidan keyingi darslar ko'rsatilmaydi.
    """
    start, next_month = month_bounds(month_start)
    lessons = Lesson.objects.filter(
        group_id=enrollment.group_id, date__gte=start, date__lt=next_month
    ).order_by("date", "start_time")
    if enrollment.start_date:
        lessons = lessons.filter(date__gte=enrollment.start_date)
    end_date = getattr(enrollment, "end_date", None)
    if end_date:
        lessons = lessons.filter(date__lte=end_date)
    lessons = list(lessons)
    marks = dict(
        Attendance.objects.filter(student=student, lesson__in=lessons).values_list("lesson_id", "status")
    )

    days = []
    for lesson in lessons:
        if lesson.status == Lesson.Status.CANCELLED:
            state = "cancelled"
        elif lesson.pk in marks:
            state = marks[lesson.pk]
        elif lesson.date >= today and lesson.status == Lesson.Status.SCHEDULED:
            state = "upcoming"
        else:
            state = "unmarked"
        days.append({"lesson": lesson, "state": state, "label": STATE_LABELS.get(state, state)})
    counts = Counter(day["state"] for day in days)
    return {
        "days": days,
        "present": counts["present"] + counts["late"],
        "absent": counts["absent"],
        "excused": counts["excused"],
        "unmarked": counts["unmarked"],
    }


def student_history(student, limit=40):
    """Karta o'zgarishlari (AuditLog) va guruh a'zoligi o'zgarishlari bitta lentada, yangilari birinchi."""
    from apps.core.models import AuditLog

    items = []
    logs = AuditLog.objects.filter(model="students.Student", object_id=str(student.pk))[:limit]
    for log in logs:
        details = ""
        if log.action == AuditLog.Action.UPDATE:
            details = ", ".join(
                f"{field}: {pair[0] or '—'} → {pair[1] or '—'}"
                for field, pair in list(log.changes.items())[:4]
            )
        items.append({
            "at": log.created_at,
            "who": log.username,
            "text": f"Karta: {log.get_action_display().lower()}",
            "details": details,
        })

    enrollment_logs = (
        EnrollmentLog.objects.filter(enrollment__student=student)
        .select_related("enrollment__group", "changed_by")
        .order_by("-changed_at")[:limit]
    )
    for log in enrollment_logs:
        status_change = ""
        if log.old_status or log.new_status:
            old = STATUS_LABELS.get(log.old_status, log.old_status or "—")
            new = STATUS_LABELS.get(log.new_status, log.new_status)
            status_change = f"{old} → {new}"
        items.append({
            "at": log.changed_at,
            "who": log.changed_by.get_username() if log.changed_by else "",
            "text": f"{log.enrollment.group.name}: {ENROLLMENT_ACTIONS.get(log.action, log.action)}",
            "details": "; ".join(part for part in (status_change, log.note) if part),
        })

    items.sort(key=lambda item: item["at"], reverse=True)
    return items[:limit]
