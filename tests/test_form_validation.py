"""SCH-01/SCH-02/GRP-01: отметка пробных учеников и валидация форм группы и расписания."""

from datetime import date, time, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.attendance.models import Attendance
from apps.attendance.services import mark_attendance
from apps.courses.models import Course
from apps.groups import grid
from apps.groups.forms import GroupForm
from apps.groups.models import Enrollment, Group
from apps.schedule.forms import (
    GenerateLessonsForm,
    RescheduleLessonForm,
    ScheduleSlotForm,
)
from apps.schedule.models import Lesson, Room, ScheduleSlot
from apps.students.models import Student
from apps.teachers.models import Teacher

pytestmark = pytest.mark.django_db


@pytest.fixture
def world():
    teacher = Teacher.objects.create(full_name="Ustoz", phone="+998900000000")
    teacher2 = Teacher.objects.create(full_name="Boshqa", phone="+998900000001")
    course = Course.objects.create(name="English", monthly_price=400000)
    group = Group.objects.create(name="ENG-1", course=course, teacher=teacher, start_date="2026-01-01", max_students=3)
    other = Group.objects.create(name="ENG-2", course=course, teacher=teacher2, start_date="2026-01-01")
    same_teacher = Group.objects.create(name="ENG-3", course=course, teacher=teacher, start_date="2026-01-01")
    room, room2 = Room.objects.create(name="101"), Room.objects.create(name="102")
    return {"teacher": teacher, "course": course, "group": group, "other": other, "same_teacher": same_teacher, "room": room, "room2": room2}


def slot_data(world, **extra):
    data = {"group": world["group"].pk, "weekday": 0, "start_time": "09:00", "end_time": "11:00", "room": world["room"].pk, "is_active": "on"}
    data.update(extra)
    return data


# ---------- SCH-01 ----------
def test_trial_student_attendance_can_be_marked(admin_user, world):
    trial = Student.objects.create(first_name="Sinov", last_name="Talaba", phone="+998901112233")
    Enrollment.objects.create(student=trial, group=world["group"], status="trial", start_date=date(2026, 3, 1))
    lesson = Lesson.objects.create(group=world["group"], date=date(2026, 3, 9), start_time=time(9), end_time=time(10), room=world["room"], teacher=world["teacher"], status="held")
    mark_attendance(lesson, trial, "present", admin_user)
    assert Attendance.objects.get(lesson=lesson, student=trial).status == "present"


def test_frozen_student_attendance_still_refused(admin_user, world):
    frozen = Student.objects.create(first_name="Muz", last_name="Talaba", phone="+998901112244")
    Enrollment.objects.create(student=frozen, group=world["group"], status="frozen", start_date=date(2026, 3, 1))
    lesson = Lesson.objects.create(group=world["group"], date=date(2026, 3, 9), start_time=time(9), end_time=time(10), room=world["room"], teacher=world["teacher"], status="held")
    with pytest.raises(Exception, match="faol a'zo emas"):
        mark_attendance(lesson, frozen, "present", admin_user)


def test_group_page_grid_lets_you_mark_trial_students(world):
    trial = Student.objects.create(first_name="Sinov", last_name="Talaba", phone="+998901112233")
    Enrollment.objects.create(student=trial, group=world["group"], status="trial", start_date=date(2026, 3, 1))
    Lesson.objects.create(group=world["group"], date=date(2026, 3, 9), start_time=time(9), end_time=time(10), room=world["room"], teacher=world["teacher"], status="held")
    data = grid.attendance_grid(world["group"], date(2026, 3, 1), date(2026, 3, 20))
    assert data["rows"][0]["cells"][0]["editable"] is True


# ---------- GRP-01 ----------
def group_form(world, **extra):
    data = {"name": "ENG-1", "course": world["course"].pk, "teacher": world["teacher"].pk, "max_students": 10,
            "start_date": "2026-01-01", "end_date": "", "status": "active"}
    data.update(extra)
    return GroupForm(data, instance=world["group"])


def test_group_form_rejects_end_before_start(world):
    form = group_form(world, end_date="2025-12-31")
    assert not form.is_valid() and "end_date" in form.errors


@pytest.mark.parametrize("value", ["0", "-3"])
def test_group_form_rejects_zero_capacity(world, value):
    form = group_form(world, max_students=value)
    assert not form.is_valid() and "max_students" in form.errors


def test_group_form_rejects_capacity_below_current_members(world):
    for n in range(3):
        s = Student.objects.create(first_name=f"S{n}", last_name="Z", phone=f"+99890000000{n}")
        Enrollment.objects.create(student=s, group=world["group"], status="active", start_date=date(2026, 3, 1))
    assert not group_form(world, max_students=2).is_valid()
    assert group_form(world, max_students=3).is_valid()


def test_group_form_valid_case(world):
    assert group_form(world, end_date="2026-12-31").is_valid()


# ---------- SCH-02 ----------
def test_slot_end_must_be_after_start(world):
    form = ScheduleSlotForm(slot_data(world, start_time="12:00", end_time="10:00"))
    assert not form.is_valid() and "end_time" in form.errors
    assert not ScheduleSlotForm(slot_data(world, start_time="10:00", end_time="10:00")).is_valid()


def test_slot_rejects_room_teacher_and_group_overlaps(world):
    ScheduleSlot.objects.create(group=world["group"], weekday=0, start_time=time(9), end_time=time(11), room=world["room"])
    # та же комната, другое время пересекается
    form = ScheduleSlotForm(slot_data(world, group=world["other"].pk, start_time="10:00", end_time="12:00"))
    assert not form.is_valid() and "room" in form.errors
    # другая комната, но тот же учитель в другой группе
    form = ScheduleSlotForm(slot_data(world, group=world["same_teacher"].pk, room=world["room2"].pk, start_time="10:00", end_time="12:00"))
    assert not form.is_valid() and "group" in form.errors
    # та же группа, другая комната, пересечение по времени
    form = ScheduleSlotForm(slot_data(world, room=world["room2"].pk, start_time="10:30", end_time="11:30"))
    assert not form.is_valid() and "start_time" in form.errors


def test_slot_back_to_back_and_other_day_are_fine(world):
    ScheduleSlot.objects.create(group=world["group"], weekday=0, start_time=time(9), end_time=time(11), room=world["room"])
    assert ScheduleSlotForm(slot_data(world, group=world["other"].pk, start_time="11:00", end_time="12:00")).is_valid()
    assert ScheduleSlotForm(slot_data(world, group=world["other"].pk, weekday=1)).is_valid()


def test_editing_a_slot_does_not_clash_with_itself(world):
    slot = ScheduleSlot.objects.create(group=world["group"], weekday=0, start_time=time(9), end_time=time(11), room=world["room"])
    assert ScheduleSlotForm(slot_data(world, end_time="11:30"), instance=slot).is_valid()


def test_inactive_slot_is_not_checked_for_overlap(world):
    ScheduleSlot.objects.create(group=world["group"], weekday=0, start_time=time(9), end_time=time(11), room=world["room"])
    data = slot_data(world, group=world["other"].pk)
    data.pop("is_active")
    assert ScheduleSlotForm(data).is_valid()


def test_generate_form_limits_range(world):
    today = timezone.localdate()
    ok = GenerateLessonsForm({"group": world["group"].pk, "date_from": today.isoformat(), "date_to": (today + timedelta(days=60)).isoformat()})
    assert ok.is_valid()
    too_long = GenerateLessonsForm({"group": world["group"].pk, "date_from": today.isoformat(), "date_to": (today + timedelta(days=3650)).isoformat()})
    assert not too_long.is_valid() and "date_to" in too_long.errors
    reversed_ = GenerateLessonsForm({"group": world["group"].pk, "date_from": today.isoformat(), "date_to": (today - timedelta(days=1)).isoformat()})
    assert not reversed_.is_valid()


def test_reschedule_form_end_after_start():
    form = RescheduleLessonForm({"new_date": "2026-10-20", "new_start_time": "12:00", "new_end_time": "11:00"})
    assert not form.is_valid() and "new_end_time" in form.errors


def test_slot_create_view_shows_error_instead_of_saving(client, admin_user, world):
    client.force_login(admin_user)
    response = client.post(reverse("schedule:slot_create"), slot_data(world, start_time="12:00", end_time="10:00"))
    assert response.status_code == 200
    assert not ScheduleSlot.objects.exists()
