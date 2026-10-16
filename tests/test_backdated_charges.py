"""Orqaga qarab qo'shilgan o'quvchi: o'tgan darslar jadval bo'yicha hisoblanadi; davomat darsni "o'tildi" qiladi."""

from datetime import date, time, timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.attendance.models import Attendance
from apps.attendance.services import mark_attendance
from apps.courses.models import Course
from apps.finance import services as finance
from apps.finance.models import LedgerEntry
from apps.groups import services as groups
from apps.groups.models import Group
from apps.schedule import services as schedule
from apps.schedule.models import Lesson, Room
from apps.students.models import Student
from apps.teachers.models import Teacher

pytestmark = pytest.mark.django_db
PRICE = 800000  # mart oyida 4 ta dars -> har biri 200 000


@pytest.fixture
def world(admin_user):
    teacher = Teacher.objects.create(full_name="Ustoz", phone="+998900000000")
    course = Course.objects.create(name="English", monthly_price=PRICE)
    group = Group.objects.create(name="ENG-1", course=course, teacher=teacher, start_date=date(2026, 1, 1))
    room = Room.objects.create(name="101")

    def student(n):
        return Student.objects.create(first_name=f"S{n}", last_name="Probe", phone=f"+99890000{n:04d}")

    def lesson(day, status="scheduled"):
        d = day if isinstance(day, date) else date(2026, 3, day)
        return Lesson.objects.create(
            group=group, date=d, start_time=time(14), end_time=time(15), room=room, teacher=teacher, status=status
        )

    return {"group": group, "student": student, "lesson": lesson, "admin": admin_user, "teacher": teacher}


def charged(student):
    return -sum(e.amount for e in LedgerEntry.objects.filter(student=student, kind="charge"))


def enroll_backdated(world, student):
    """A'zolik bugundan yoziladi (backfill bo'sh), keyin boshlanish martga suriladi: eski holatni taqlid qiladi."""
    e = groups.enroll_student(student, world["group"], start_date=timezone.localdate(), user=world["admin"])
    e.start_date = e.paid_from = date(2026, 3, 1)
    e.save(update_fields=["start_date", "paid_from"])
    return e


def test_backdated_enrollment_charges_past_unmarked_lessons(world):
    lessons = [world["lesson"](d) for d in (2, 4, 6, 9)]
    s = world["student"](1)
    enrollment = groups.enroll_student(s, world["group"], start_date=date(2026, 3, 1), user=world["admin"])
    assert enrollment.backfilled_charges == 4
    assert charged(s) == PRICE
    # dars holati o'zgarmaydi
    assert all(Lesson.objects.get(pk=lesson.pk).status == "scheduled" for lesson in lessons)


def test_today_and_future_lessons_are_not_backfilled(world):
    today = timezone.localdate()
    world["lesson"](today - timedelta(days=1))
    world["lesson"](today)
    world["lesson"](today + timedelta(days=2))
    s = world["student"](2)
    enrollment = groups.enroll_student(s, world["group"], start_date=today - timedelta(days=3), user=world["admin"])
    assert enrollment.backfilled_charges == 1


def test_later_held_mark_does_not_double_charge_and_charges_others(world):
    lesson = world["lesson"](2)
    old = world["student"](3)
    enroll_backdated(world, old)
    new = world["student"](4)
    groups.enroll_student(new, world["group"], start_date=date(2026, 3, 1), user=world["admin"])
    assert LedgerEntry.objects.filter(student=old, kind="charge").count() == 0  # boshqalarga tegmaydi
    schedule.mark_lesson_held(lesson, world["admin"])
    assert LedgerEntry.objects.filter(student=new, lesson=lesson, kind="charge").count() == 1
    assert LedgerEntry.objects.filter(student=old, lesson=lesson, kind="charge").count() == 1


def test_cancel_returns_backfilled_charge(world):
    lesson = world["lesson"](2)
    s = world["student"](5)
    groups.enroll_student(s, world["group"], start_date=date(2026, 3, 1), user=world["admin"])
    assert finance.get_balance(s) == -PRICE
    schedule.cancel_lesson(lesson, reason="o'qituvchi kasal", user=world["admin"])
    assert finance.get_balance(s) == 0


def test_trial_and_cancelled_lessons_not_backfilled(world):
    world["lesson"](2)
    world["lesson"](4, status="cancelled")
    trial = groups.enroll_student(
        world["student"](6), world["group"], start_date=date(2026, 3, 1), status="trial", user=world["admin"]
    )
    assert trial.backfilled_charges == 0
    active = groups.enroll_student(world["student"](7), world["group"], start_date=date(2026, 3, 1), user=world["admin"])
    assert active.backfilled_charges == 1


def test_attendance_marks_past_lesson_held_and_charges_everyone(world):
    lesson = world["lesson"](2)
    a, b = world["student"](8), world["student"](9)
    enroll_backdated(world, a)
    enroll_backdated(world, b)
    att = mark_attendance(lesson, a, "present", world["admin"])
    lesson.refresh_from_db()
    assert lesson.status == "held" and att.lesson_marked_held
    assert LedgerEntry.objects.filter(lesson=lesson, kind="charge").count() == 2


def test_attendance_on_future_lesson_does_not_hold(world):
    today = timezone.localdate()
    lesson = world["lesson"](today + timedelta(days=1))
    s = world["student"](10)
    groups.enroll_student(s, world["group"], start_date=today, user=world["admin"])
    mark_attendance(lesson, s, "present", world["admin"])
    lesson.refresh_from_db()
    assert lesson.status == "scheduled"


def test_teacher_marking_own_group_holds_lesson(client, world, teacher_user):
    world["teacher"].user = teacher_user
    world["teacher"].save(update_fields=["user"])
    today = timezone.localdate()
    lesson = world["lesson"](today)
    s = world["student"](11)
    groups.enroll_student(s, world["group"], start_date=today, user=world["admin"])
    client.force_login(teacher_user)
    r = client.post(
        reverse("groups:group_attendance", args=[world["group"].pk]),
        {"lesson_id": lesson.pk, "student_id": s.pk, "status": "present"},
    )
    assert r.status_code == 200 and r.json()["lesson_held"] is True
    lesson.refresh_from_db()
    assert lesson.status == "held"
    assert LedgerEntry.objects.filter(student=s, lesson=lesson, kind="charge").exists()


def test_enroll_message_shows_amount_and_warns_when_no_lessons(client, world, admin_user):
    client.force_login(admin_user)
    world["lesson"](2)
    s = world["student"](12)
    r = client.post(
        reverse("groups:group_enroll", args=[world["group"].pk]),
        {"student": s.pk, "status": "active", "start_date": "2026-03-01"},
        follow=True,
    )
    text = " ".join(str(m) for m in r.context["messages"])
    assert "1 ta dars uchun" in text and "800 000 so'm" in text
    empty = Group.objects.create(name="ENG-2", course=world["group"].course, teacher=world["teacher"], start_date=date(2026, 1, 1))
    r = client.post(
        reverse("groups:group_enroll", args=[empty.pk]),
        {"student": world["student"](13).pk, "status": "active", "start_date": "2026-03-01"},
        follow=True,
    )
    assert "jadvalda dars yo'q" in " ".join(str(m) for m in r.context["messages"])


def test_hold_attended_lessons_command(world):
    marked, unmarked = world["lesson"](2), world["lesson"](4)
    s = world["student"](14)
    enroll_backdated(world, s)
    Attendance.objects.create(lesson=marked, student=s, status="present", marked_by=world["admin"])  # eski davomat
    out = StringIO()
    call_command("hold_attended_lessons", stdout=out)
    marked.refresh_from_db()
    assert marked.status == "scheduled" and "1 ta dars topildi" in out.getvalue()
    call_command("hold_attended_lessons", "--apply", stdout=StringIO())
    marked.refresh_from_db()
    unmarked.refresh_from_db()
    assert marked.status == "held" and unmarked.status == "scheduled"
    assert LedgerEntry.objects.filter(student=s, lesson=marked, kind="charge").exists()
