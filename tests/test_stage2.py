"""Tests for Stage 2: schedule and attendance services.

Run with:
  TEST_DB=sqlite pytest tests/test_stage2.py -v
"""

from datetime import date, time, timedelta

import pytest
from django.core.exceptions import ValidationError

# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def admin_user(db):
    from apps.accounts.models import User
    return User.objects.create_user(
        username="admin2",
        password="pass",
        role=User.Role.ADMINISTRATOR,
    )


@pytest.fixture
def teacher_obj(db):
    from apps.teachers.models import Teacher
    return Teacher.objects.create(full_name="Sardor Mirzayev", phone="+998901234567")


@pytest.fixture
def course(db):
    from apps.courses.models import Course
    return Course.objects.create(
        name="Python kursi",
        monthly_price=500_000,
    )


@pytest.fixture
def group(db, teacher_obj, course):
    from apps.groups.models import Group
    return Group.objects.create(
        name="G-01",
        course=course,
        teacher=teacher_obj,
        max_students=10,
        start_date=date.today(),
    )


@pytest.fixture
def room(db):
    from apps.schedule.models import Room
    return Room.objects.create(name="101-xona")


@pytest.fixture
def slot(db, group, room):
    from apps.schedule.models import ScheduleSlot
    # Monday=0 slot
    return ScheduleSlot.objects.create(
        group=group,
        weekday=0,  # Monday
        start_time=time(9, 0),
        end_time=time(11, 0),
        room=room,
        is_active=True,
    )


@pytest.fixture
def student(db):
    from apps.students.models import Student
    return Student.objects.create(
        first_name="Ali",
        last_name="Valiyev",
        phone="+998901111111",
        joined_date=date.today(),
    )


@pytest.fixture
def enrollment(db, student, group):
    from apps.groups.models import Enrollment
    return Enrollment.objects.create(
        student=student,
        group=group,
        status=Enrollment.Status.ACTIVE,
        start_date=date.today() - timedelta(days=30),
    )


# ── schedule.services tests ──────────────────────────────────────────────────

class TestGenerateLessons:
    def test_generates_lessons_for_range(self, db, slot, group):
        from apps.schedule.services import generate_lessons

        monday = date(2026, 9, 28)  # known Monday
        sunday = monday + timedelta(days=6)

        created = generate_lessons(group, monday, sunday)
        assert len(created) == 1  # only one Monday in the week
        assert created[0].date == monday
        assert created[0].status == "scheduled"

    def test_idempotent_no_duplicates(self, db, slot, group):
        from apps.schedule.services import generate_lessons

        monday = date(2026, 9, 28)
        sunday = monday + timedelta(days=6)

        generate_lessons(group, monday, sunday)
        again = generate_lessons(group, monday, sunday)
        assert again == []  # nothing new

    def test_invalid_date_range_raises(self, db, slot, group):
        from apps.schedule.services import generate_lessons

        with pytest.raises(ValidationError):
            generate_lessons(group, date(2026, 10, 5), date(2026, 10, 1))

    def test_room_conflict_raises(self, db, slot, group, room, teacher_obj, course):
        """Two groups, same room same time — second generate should raise."""
        from apps.groups.models import Group
        from apps.schedule.models import ScheduleSlot
        from apps.schedule.services import generate_lessons

        group2 = Group.objects.create(
            name="G-02",
            course=course,
            teacher=teacher_obj,
            max_students=5,
            start_date=date.today(),
        )
        ScheduleSlot.objects.create(
            group=group2,
            weekday=0,  # same Monday
            start_time=time(9, 0),
            end_time=time(11, 0),
            room=room,  # same room!
            is_active=True,
        )

        monday = date(2026, 9, 28)
        sunday = monday + timedelta(days=6)

        # First group: OK
        generate_lessons(group, monday, sunday)

        # Second group: conflict
        with pytest.raises(ValidationError, match="Xona to'qnashuvi"):
            generate_lessons(group2, monday, sunday)


class TestCancelLesson:
    def test_cancel_sets_status(self, db, slot, group):
        from apps.schedule.services import cancel_lesson, generate_lessons

        monday = date(2026, 9, 28)
        lessons = generate_lessons(group, monday, monday)
        lesson = lessons[0]

        cancelled = cancel_lesson(lesson, reason="O'qituvchi kasal")
        assert cancelled.status == "cancelled"
        assert "O'qituvchi kasal" in cancelled.note

    def test_cancel_already_cancelled_raises(self, db, slot, group):
        from apps.schedule.services import cancel_lesson, generate_lessons

        monday = date(2026, 9, 28)
        lessons = generate_lessons(group, monday, monday)
        lesson = lessons[0]

        cancel_lesson(lesson)
        with pytest.raises(ValidationError):
            cancel_lesson(lesson)


class TestRescheduleLesson:
    def test_reschedule_changes_fields(self, db, slot, group, room):
        from apps.schedule.services import generate_lessons, reschedule_lesson

        monday = date(2026, 9, 28)
        lessons = generate_lessons(group, monday, monday)
        lesson = lessons[0]

        new_date = monday + timedelta(days=1)  # Tuesday
        reschedule_lesson(
            lesson,
            new_date=new_date,
            new_start_time=time(14, 0),
            new_end_time=time(16, 0),
        )
        lesson.refresh_from_db()
        assert lesson.date == new_date
        assert lesson.start_time == time(14, 0)

    def test_reschedule_cancelled_raises(self, db, slot, group):
        from apps.schedule.services import (
            cancel_lesson,
            generate_lessons,
            reschedule_lesson,
        )

        monday = date(2026, 9, 28)
        lessons = generate_lessons(group, monday, monday)
        lesson = lessons[0]
        cancel_lesson(lesson)

        with pytest.raises(ValidationError):
            reschedule_lesson(lesson, new_date=monday, new_start_time=time(10, 0), new_end_time=time(12, 0))


# ── attendance.services tests ────────────────────────────────────────────────

class TestMarkAttendance:
    def test_mark_present(self, db, slot, group, student, enrollment, admin_user):
        from apps.attendance.models import Attendance, AttendanceLog
        from apps.attendance.services import mark_attendance
        from apps.schedule.services import generate_lessons

        monday = date(2026, 9, 28)
        lessons = generate_lessons(group, monday, monday)
        lesson = lessons[0]

        att = mark_attendance(lesson, student, Attendance.Status.PRESENT, admin_user)
        assert att.status == Attendance.Status.PRESENT
        assert AttendanceLog.objects.filter(attendance=att).count() == 1

    def test_mark_creates_log_on_change(self, db, slot, group, student, enrollment, admin_user):
        from apps.attendance.models import Attendance, AttendanceLog
        from apps.attendance.services import mark_attendance
        from apps.schedule.services import generate_lessons

        monday = date(2026, 9, 28)
        lessons = generate_lessons(group, monday, monday)
        lesson = lessons[0]

        mark_attendance(lesson, student, Attendance.Status.PRESENT, admin_user)
        mark_attendance(lesson, student, Attendance.Status.ABSENT, admin_user)

        assert AttendanceLog.objects.count() == 2

    def test_same_status_no_extra_log(self, db, slot, group, student, enrollment, admin_user):
        from apps.attendance.models import Attendance, AttendanceLog
        from apps.attendance.services import mark_attendance
        from apps.schedule.services import generate_lessons

        monday = date(2026, 9, 28)
        lessons = generate_lessons(group, monday, monday)
        lesson = lessons[0]

        mark_attendance(lesson, student, Attendance.Status.PRESENT, admin_user)
        mark_attendance(lesson, student, Attendance.Status.PRESENT, admin_user)  # no change

        assert AttendanceLog.objects.count() == 1

    def test_cannot_mark_cancelled_lesson(self, db, slot, group, student, enrollment, admin_user):
        from apps.attendance.models import Attendance
        from apps.attendance.services import mark_attendance
        from apps.schedule.services import cancel_lesson, generate_lessons

        monday = date(2026, 9, 28)
        lessons = generate_lessons(group, monday, monday)
        lesson = lessons[0]
        cancel_lesson(lesson)

        with pytest.raises(ValidationError, match="Bekor qilingan"):
            mark_attendance(lesson, student, Attendance.Status.PRESENT, admin_user)

    def test_cannot_mark_non_enrolled_student(self, db, slot, group, admin_user):
        from apps.attendance.models import Attendance
        from apps.attendance.services import mark_attendance
        from apps.schedule.services import generate_lessons
        from apps.students.models import Student

        # Student not enrolled in this group
        other = Student.objects.create(
            first_name="Baxrom",
            last_name="Karimov",
            phone="+998902222222",
            joined_date=date.today(),
        )

        monday = date(2026, 9, 28)
        lessons = generate_lessons(group, monday, monday)
        lesson = lessons[0]

        with pytest.raises(ValidationError, match="faol a'zo emas"):
            mark_attendance(lesson, other, Attendance.Status.PRESENT, admin_user)


class TestGetAttendanceStats:
    def test_stats_empty(self, db, student):
        from apps.attendance.services import get_attendance_stats

        stats = get_attendance_stats(student)
        assert stats["total"] == 0
        assert stats["percent"] == 0

    def test_stats_after_marking(self, db, slot, group, student, enrollment, admin_user):
        from apps.attendance.models import Attendance
        from apps.attendance.services import get_attendance_stats, mark_attendance
        from apps.schedule.services import generate_lessons, mark_lesson_held

        monday = date(2026, 9, 28)
        lessons = generate_lessons(group, monday, monday)
        lesson = lessons[0]

        # Mark lesson as held so stats count it
        mark_lesson_held(lesson, admin_user)

        mark_attendance(lesson, student, Attendance.Status.PRESENT, admin_user)

        stats = get_attendance_stats(student)
        assert stats["total"] == 1
        assert stats["present"] == 1
        assert stats["percent"] == 100
