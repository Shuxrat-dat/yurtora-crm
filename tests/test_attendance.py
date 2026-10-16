"""Davomat: belgilash qoidalari, o'zgartirish jurnali, statistika va huquqlar."""

from datetime import date, time

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.accounts.models import User
from apps.attendance.models import Attendance, AttendanceLog
from apps.attendance.services import get_attendance_stats, mark_attendance
from apps.courses.models import Course
from apps.groups.models import Enrollment, Group
from apps.schedule.models import Lesson, Room
from apps.students.models import Student
from apps.teachers.models import Teacher

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(teacher_user, password):
    teacher = Teacher.objects.create(user=teacher_user, full_name="Ustoz", phone="+998900000000")
    other_user = User.objects.create_user("t2", password=password, role="teacher")
    other = Teacher.objects.create(user=other_user, full_name="Boshqa", phone="+998900000001")
    course = Course.objects.create(name="English", monthly_price=400000)
    group = Group.objects.create(name="ENG-1", course=course, teacher=teacher, start_date="2026-01-01")
    foreign_group = Group.objects.create(name="ENG-2", course=course, teacher=other, start_date="2026-01-01")
    room = Room.objects.create(name="101")
    student = Student.objects.create(first_name="Ali", last_name="Valiyev", phone="+998901112233")
    Enrollment.objects.create(student=student, group=group, status="active", start_date=date(2026, 3, 1))

    def lesson(day=10, g=None, status="held", month=3):
        g = g or group
        return Lesson.objects.create(group=g, date=date(2026, month, day), start_time=time(9), end_time=time(10), room=room, teacher=g.teacher, status=status)

    return {"teacher": teacher, "other_user": other_user, "group": group, "foreign": foreign_group, "student": student, "lesson": lesson}


def test_mark_creates_record_and_log(admin_user, world):
    lesson = world["lesson"]()
    attendance = mark_attendance(lesson, world["student"], "present", admin_user)
    assert attendance.status == "present" and attendance.marked_by == admin_user
    log = AttendanceLog.objects.get(attendance=attendance)
    assert (log.old_status, log.new_status, log.changed_by) == ("", "present", admin_user)


def test_change_is_logged_and_same_status_is_a_noop(admin_user, world):
    lesson = world["lesson"]()
    mark_attendance(lesson, world["student"], "present", admin_user)
    mark_attendance(lesson, world["student"], "present", admin_user)  # без изменений
    assert AttendanceLog.objects.count() == 1
    mark_attendance(lesson, world["student"], "absent", admin_user)
    assert Attendance.objects.count() == 1  # запись одна, меняется статус
    assert list(AttendanceLog.objects.order_by("id").values_list("old_status", "new_status")) == [("", "present"), ("present", "absent")]


@pytest.mark.parametrize("status", ["", "boom", "PRESENT", "none"])
def test_invalid_status_is_rejected(admin_user, world, status):
    with pytest.raises(ValidationError):
        mark_attendance(world["lesson"](), world["student"], status, admin_user)
    assert not Attendance.objects.exists()


def test_cancelled_lesson_cannot_be_marked(admin_user, world):
    with pytest.raises(ValidationError, match="Bekor qilingan"):
        mark_attendance(world["lesson"](status="cancelled"), world["student"], "present", admin_user)


def test_student_must_be_enrolled_on_the_lesson_date(admin_user, world):
    before = world["lesson"](day=20, month=2)  # запись начинается 01.03
    with pytest.raises(ValidationError, match="faol a'zo emas"):
        mark_attendance(before, world["student"], "present", admin_user)
    stranger = Student.objects.create(first_name="Chet", last_name="El", phone="+998901112299")
    with pytest.raises(ValidationError):
        mark_attendance(world["lesson"](), stranger, "present", admin_user)


def test_enrollment_end_date_limits_marking(admin_user, world):
    Enrollment.objects.filter(student=world["student"]).update(end_date=date(2026, 3, 15), status="active")
    assert mark_attendance(world["lesson"](day=15), world["student"], "present", admin_user)
    with pytest.raises(ValidationError):
        mark_attendance(world["lesson"](day=16), world["student"], "present", admin_user)


def test_finished_enrollment_cannot_be_marked(admin_user, world):
    Enrollment.objects.filter(student=world["student"]).update(status="finished", end_date=date(2026, 3, 31))
    with pytest.raises(ValidationError):
        mark_attendance(world["lesson"](), world["student"], "present", admin_user)


def test_stats_count_only_held_lessons(admin_user, world):
    for day, status in ((2, "present"), (3, "late"), (4, "absent"), (5, "excused")):
        mark_attendance(world["lesson"](day=day), world["student"], status, admin_user)
    scheduled = world["lesson"](day=6, status="scheduled")
    Attendance.objects.create(lesson=scheduled, student=world["student"], status="present")  # не считается
    stats = get_attendance_stats(world["student"])
    assert stats == {"total": 4, "present": 1, "late": 1, "absent": 1, "excused": 1, "attended": 2, "percent": 50}
    empty = Student.objects.create(first_name="Yangi", last_name="Z", phone="+998901112288")
    assert get_attendance_stats(empty)["percent"] == 0


def test_unique_attendance_per_lesson_and_student(world):
    from django.db import IntegrityError, transaction

    lesson = world["lesson"]()
    Attendance.objects.create(lesson=lesson, student=world["student"], status="present")
    with pytest.raises(IntegrityError), transaction.atomic():
        Attendance.objects.create(lesson=lesson, student=world["student"], status="absent")


# ---------- права на отметку через представление урока ----------
def post_mark(client, lesson, student, status="present"):
    return client.post(reverse("schedule:mark_attendance", args=[lesson.pk]), {"student_id": student.pk, "status": status})


def test_teacher_marks_own_lesson_only(client, teacher_user, world):
    own = world["lesson"]()
    foreign = world["lesson"](g=world["foreign"])
    client.force_login(teacher_user)
    assert post_mark(client, own, world["student"]).status_code in (200, 302)
    assert Attendance.objects.filter(lesson=own).exists()
    assert post_mark(client, foreign, world["student"]).status_code == 403
    assert not Attendance.objects.filter(lesson=foreign).exists()


def test_teacher_without_profile_is_denied(client, world, password):
    nobody = User.objects.create_user("nobody", password=password, role="teacher")
    client.force_login(nobody)
    assert post_mark(client, world["lesson"](), world["student"]).status_code == 403


def test_anonymous_is_redirected_to_login(client, world):
    assert post_mark(client, world["lesson"](), world["student"]).status_code == 302
    assert not Attendance.objects.exists()


def test_admin_and_director_may_mark_any_lesson(client, admin_user, director_user, world):
    lesson = world["lesson"](g=world["foreign"])
    Enrollment.objects.create(student=world["student"], group=world["foreign"], status="active", start_date=date(2026, 3, 1))
    client.force_login(admin_user)
    assert post_mark(client, lesson, world["student"], "late").status_code in (200, 302)
    client.force_login(director_user)
    assert post_mark(client, lesson, world["student"], "absent").status_code in (200, 302)
    assert Attendance.objects.get(lesson=lesson).status == "absent"
