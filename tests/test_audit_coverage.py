"""AUD-01: журнал действий видит уроки, зарплатные операции, настройки чека и чаты сотрудников."""

from contextlib import contextmanager
from datetime import date, time

import pytest
from django.urls import reverse

from apps.core import audit
from apps.core.models import AuditLog
from apps.courses.models import Course
from apps.finance import services as finance
from apps.groups.models import Enrollment, Group
from apps.notifications.models import AdminChat
from apps.schedule.models import Lesson, Room, ScheduleSlot
from apps.students.models import Student
from apps.teachers.models import Teacher

pytestmark = pytest.mark.django_db


@pytest.fixture
def world():
    teacher = Teacher.objects.create(full_name="Ustoz", phone="+998900000000", pay_type="fixed", monthly_salary=3000000)
    course = Course.objects.create(name="English", monthly_price=400000)
    group = Group.objects.create(name="ENG-1", course=course, teacher=teacher, start_date="2026-01-01")
    room = Room.objects.create(name="101")
    student = Student.objects.create(first_name="Ali", last_name="Valiyev", phone="+998901112233")
    Enrollment.objects.create(student=student, group=group, status="active", start_date=date(2026, 3, 1), paid_from=date(2026, 3, 1))
    lesson = Lesson.objects.create(group=group, date=date(2026, 3, 9), start_time=time(9), end_time=time(10), room=room, teacher=teacher)
    return {"teacher": teacher, "group": group, "lesson": lesson, "room": room}


@contextmanager
def acting_as(user):
    """Имитирует запрос: журнал пишет только изменения, сделанные от имени пользователя."""
    token = audit._current.set({"user": user, "ip": "127.0.0.1"})
    try:
        yield
    finally:
        audit._current.reset(token)


def rows(model):
    return AuditLog.objects.filter(model=model)


def test_lesson_held_and_cancel_are_logged_with_who(client, director_user, world):
    client.force_login(director_user)
    lesson = world["lesson"]
    client.post(reverse("schedule:mark_lesson_held", args=[lesson.pk]))
    held = rows("schedule.Lesson").get(action="update")
    assert held.username == director_user.username and held.changes["status"] == ["scheduled", "held"]
    client.post(reverse("schedule:cancel_lesson", args=[lesson.pk]), {"reason": "bayram"})
    cancelled = rows("schedule.Lesson").filter(action="update").latest("id")
    assert cancelled.changes["status"] == ["held", "cancelled"]


def test_reschedule_is_logged(client, director_user, world):
    client.force_login(director_user)
    client.post(
        reverse("schedule:reschedule_lesson", args=[world["lesson"].pk]),
        {"new_date": "2026-03-12", "new_start_time": "10:00", "new_end_time": "11:00", "reason": "x"},
    )
    assert rows("schedule.Lesson").latest("id").changes["date"] == ["2026-03-09", "2026-03-12"]


def test_bulk_lesson_generation_does_not_flood_the_log(client, director_user, world):
    ScheduleSlot.objects.create(group=world["group"], weekday=0, start_time=time(14), end_time=time(15), room=world["room"])
    client.force_login(director_user)
    client.post(reverse("schedule:generate_lessons"), {"group": world["group"].pk, "date_from": "2026-04-01", "date_to": "2026-05-31"})
    assert Lesson.objects.count() > 5
    assert not rows("schedule.Lesson").filter(action="create").exists()


def test_payroll_close_is_logged(client, director_user, world):
    client.force_login(director_user)
    client.post(reverse("finance:payroll_close"), {"year": 2026, "month": 3})
    closed = rows("finance.PayrollMonth")
    assert closed.exists() and all(r.username == director_user.username for r in closed)


def test_salary_adjustment_and_payout_are_logged(director_user, world):
    with acting_as(director_user):
        finance.add_payroll_adjustment(2026, 4, world["teacher"], 100000, "mukofot", director_user)
        finance.register_salary_payout(world["teacher"], 2026, 4, 500000, "cash", date(2026, 4, 30), "avans", director_user)
    assert rows("finance.PayrollAdjustment").filter(action="create", username=director_user.username).exists()
    assert rows("finance.SalaryPayout").filter(action="create", username=director_user.username).exists()


def test_receipt_settings_change_is_logged(client, director_user):
    client.force_login(director_user)
    client.post(reverse("finance:receipt_settings"), {"address": "Yangi manzil", "phone": "+998901112233", "footer_text": "x", "logo_position": "top"})
    row = rows("finance.ReceiptSettings").latest("id")
    assert row.username == director_user.username and "address" in str(row.changes)


def test_admin_chat_switch_off_is_logged(client, director_user, admin_user):
    chat = AdminChat.objects.create(user=admin_user, chat_id=77, is_active=True)
    client.force_login(director_user)
    client.post(reverse("accounts:staff_edit", args=[admin_user.pk]), {"first_name": "A", "last_name": "B", "role": "teacher"})
    chat.refresh_from_db()
    assert chat.is_active is False
    row = rows("notifications.AdminChat").filter(action="update").latest("id")
    assert row.username == director_user.username and row.changes["is_active"] == ["True", "False"]
