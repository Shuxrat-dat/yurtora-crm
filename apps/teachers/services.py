"""O'qituvchi profili uchun ma'lumotlar: ko'rsatkichlar, ish navbati, e'tibor talab qiladigan o'quvchilar, guruhlar.

Hamma narsa mavjud modellardan hisoblanadi. Maosh va moliyaviy ma'lumotlar bu yerda yo'q
(ish haqi faqat direktor uchun).
"""

from collections import defaultdict
from datetime import timedelta

from django.db.models import Count, Prefetch, Q
from django.utils import timezone

from apps.attendance.models import Attendance
from apps.core.branding import center_name
from apps.groups.models import Enrollment, Group
from apps.schedule.models import Lesson, ScheduleSlot
from apps.students.models import Student

QUEUE_DAYS = 14             # ish navbati: oxirgi necha kunlik darslar
ATTENTION_DAYS = 30         # e'tibor talab qiladi: oxirgi necha kun
ATTENTION_MIN_ABSENCES = 3  # shu miqdordan ko'p dars qoldirgan o'quvchi
ATTENTION_LIMIT = 10
WEEKDAY_SHORT = ["Du", "Se", "Chor", "Pay", "Ju", "Sha", "Yak"]

QUEUE_ATTENDANCE = "attendance"  # dars o'tilgan, ammo davomat to'liq belgilanmagan
QUEUE_STATUS = "status"          # dars sanasi o'tgan, ammo "o'tildi" deb belgilanmagan


def build_teacher_profile(teacher, show_archive=False, now=None):
    """Profil sahifasi uchun barcha bloklar."""
    now = now or timezone.localtime()
    today = now.date()
    active_groups = Group.objects.filter(teacher=teacher, status=Group.Status.ACTIVE)
    return {
        "info": _info(teacher, active_groups),
        "queue": _work_queue(teacher, today),
        "attention": _attention_students(teacher, today),
        "lesson_now": _current_or_next_lesson(teacher, now),
        "lessons_today": _lessons_today(teacher, today),
        "groups": _groups(teacher, show_archive, today),
    }


def _info(teacher, active_groups):
    students_count = (
        Enrollment.objects.filter(
            group__in=active_groups,
            status=Enrollment.Status.ACTIVE,
            student__status=Student.Status.ACTIVE,
        )
        .values("student_id")
        .distinct()
        .count()
    )
    return {
        "registered": timezone.localtime(teacher.created_at).date(),
        "branch": center_name(),
        "courses_count": active_groups.values("course_id").distinct().count(),
        "groups_count": active_groups.count(),
        "students_count": students_count,
        "phone": teacher.phone,
    }


def _work_queue(teacher, today):
    """Darslar, bo'yicha o'qituvchining ishi qolgan: davomat yoki dars holati."""
    start = today - timedelta(days=QUEUE_DAYS)
    lessons = list(
        Lesson.objects.filter(teacher=teacher, date__gte=start, date__lte=today)
        .exclude(status=Lesson.Status.CANCELLED)
        .select_related("group")
        .order_by("-date", "-start_time")
    )
    held = [lesson for lesson in lessons if lesson.status == Lesson.Status.HELD]

    marked = defaultdict(set)
    expected = defaultdict(list)
    if held:
        for lesson_id, student_id in Attendance.objects.filter(lesson__in=held).values_list(
            "lesson_id", "student_id"
        ):
            marked[lesson_id].add(student_id)
        enrollments = Enrollment.objects.filter(
            group_id__in={lesson.group_id for lesson in held},
            status=Enrollment.Status.ACTIVE,
            student__status=Student.Status.ACTIVE,
        ).values_list("group_id", "student_id", "start_date", "end_date")
        for group_id, student_id, start_date, end_date in enrollments:
            expected[group_id].append((student_id, start_date, end_date))

    items = []
    for lesson in lessons:
        if lesson.status == Lesson.Status.HELD:
            expected_ids = {
                student_id
                for student_id, start_date, end_date in expected[lesson.group_id]
                if start_date <= lesson.date and (end_date is None or end_date >= lesson.date)
            }
            missing = len(expected_ids - marked[lesson.pk])
            if missing:
                items.append({"lesson": lesson, "kind": QUEUE_ATTENDANCE, "count": missing})
        elif lesson.status == Lesson.Status.SCHEDULED and lesson.date < today:
            items.append({"lesson": lesson, "kind": QUEUE_STATUS, "count": 0})
    return items


def _attention_students(teacher, today):
    """Oxirgi kunlarda ko'p dars qoldirgan faol o'quvchilar (faqat ism, telefonsiz)."""
    start = today - timedelta(days=ATTENTION_DAYS)
    rows = (
        Attendance.objects.filter(
            lesson__teacher=teacher,
            lesson__date__gte=start,
            lesson__date__lte=today,
            status=Attendance.Status.ABSENT,
            student__status=Student.Status.ACTIVE,
        )
        .values("student_id", "student__first_name", "student__last_name", "lesson__group__name")
        .annotate(absences=Count("id"))
        .filter(absences__gte=ATTENTION_MIN_ABSENCES)
        .order_by("-absences", "student__last_name")[:ATTENTION_LIMIT]
    )
    return [
        {
            "student_id": row["student_id"],
            "name": f"{row['student__last_name']} {row['student__first_name']}".strip(),
            "group": row["lesson__group__name"],
            "absences": row["absences"],
        }
        for row in rows
    ]


def _current_or_next_lesson(teacher, now):
    """Hozir davom etayotgan yoki eng yaqin keyingi dars."""
    today = now.date()
    todays = list(
        Lesson.objects.filter(teacher=teacher, date=today)
        .exclude(status=Lesson.Status.CANCELLED)
        .select_related("group", "room")
        .order_by("start_time")
    )
    current_time = now.time()
    for lesson in todays:
        if lesson.start_time <= current_time <= lesson.end_time:
            return {"lesson": lesson, "state": "current"}
    for lesson in todays:
        if lesson.start_time > current_time:
            return {"lesson": lesson, "state": "next"}
    upcoming = (
        Lesson.objects.filter(teacher=teacher, date__gt=today)
        .exclude(status=Lesson.Status.CANCELLED)
        .select_related("group", "room")
        .order_by("date", "start_time")
        .first()
    )
    return {"lesson": upcoming, "state": "next"} if upcoming else None


def _groups(teacher, show_archive, today):
    statuses = [Group.Status.CLOSED] if show_archive else [Group.Status.ACTIVE]
    groups = (
        Group.objects.filter(teacher=teacher, status__in=statuses)
        .select_related("course")
        .annotate(
            students_count=Count(
                "enrollments",
                filter=Q(enrollments__status=Enrollment.Status.ACTIVE),
                distinct=True,
            ),
            total_count=Count("enrollments", distinct=True),
            trial_count=Count(
                "enrollments",
                filter=Q(enrollments__status=Enrollment.Status.TRIAL),
                distinct=True,
            ),
        )
        .prefetch_related(
            Prefetch(
                "schedule_slots",
                queryset=ScheduleSlot.objects.filter(is_active=True).select_related("room").order_by(
                    "weekday", "start_time"
                ),
                to_attr="active_slots",
            )
        )
        .order_by("name")
    )
    return [
        {
            "group": group,
            "students_count": group.students_count,
            "total_count": group.total_count,
            "trial_count": group.trial_count,
            "months": _months_between(group.start_date, today),
            "time": _time_range(group.active_slots),
            "days": " · ".join(dict.fromkeys(WEEKDAY_SHORT[slot.weekday] for slot in group.active_slots)),
            "rooms": ", ".join(dict.fromkeys(slot.room.name for slot in group.active_slots)),
            "schedule": ", ".join(
                f"{WEEKDAY_SHORT[slot.weekday]} {slot.start_time:%H:%M}"
                for slot in group.active_slots
            ),
        }
        for group in groups
    ]


def _months_between(start, today):
    """Guruh necha oydan beri ishlayapti (kamida 1)."""
    return max(1, (today.year - start.year) * 12 + today.month - start.month)


def _time_range(slots):
    """Guruhning dars vaqti: hamma kunlar bir xil bo'lsa "09:00–11:00", aks holda birinchisi."""
    return f"{slots[0].start_time:%H:%M}–{slots[0].end_time:%H:%M}" if slots else ""


ATTENDANCE_BUTTONS = [
    (Attendance.Status.PRESENT, "Keldi"),
    (Attendance.Status.LATE, "Kechikdi"),
    (Attendance.Status.EXCUSED, "Sababli"),
    (Attendance.Status.ABSENT, "Kelmadi"),
]


def _lessons_today(teacher, today):
    """Bugungi darslar va ularning o'quvchilari joriy davomat holati bilan: o'qituvchi profildan belgilaydi."""
    lessons = list(
        Lesson.objects.filter(teacher=teacher, date=today)
        .exclude(status=Lesson.Status.CANCELLED)
        .select_related("group", "room")
        .order_by("start_time")
    )
    if not lessons:
        return []
    marks = {
        (lesson_id, student_id): status
        for lesson_id, student_id, status in Attendance.objects.filter(lesson__in=lessons).values_list(
            "lesson_id", "student_id", "status"
        )
    }
    enrollments = (
        Enrollment.objects.filter(
            group_id__in={lesson.group_id for lesson in lessons},
            status__in=[Enrollment.Status.ACTIVE, Enrollment.Status.TRIAL],
            start_date__lte=today,
        )
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=today))
        .select_related("student")
        .order_by("student__last_name", "student__first_name")
    )
    by_group = defaultdict(list)
    for enrollment in enrollments:
        by_group[enrollment.group_id].append(enrollment)
    result = []
    for lesson in lessons:
        rows = [
            {
                "student": e.student,
                "trial": e.status == Enrollment.Status.TRIAL,
                "status": marks.get((lesson.pk, e.student_id), ""),
            }
            for e in by_group[lesson.group_id]
        ]
        result.append(
            {
                "lesson": lesson,
                "rows": rows,
                "marked": sum(1 for r in rows if r["status"]),
                "buttons": ATTENDANCE_BUTTONS,
            }
        )
    return result
