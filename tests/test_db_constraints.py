"""DB-01: CHECK-ограничения работают на уровне БД, а формы выдают понятные ошибки вместо 500."""

from datetime import date, time

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse

from apps.courses.models import Course
from apps.finance.models import ExpenseCategory, ExpenseItem, LedgerEntry
from apps.groups.models import Enrollment, Group
from apps.schedule.models import Lesson, Room, ScheduleSlot
from apps.students.models import Student
from apps.teachers.models import Teacher

pytestmark = pytest.mark.django_db


@pytest.fixture
def base():
    teacher = Teacher.objects.create(full_name="Ustoz", phone="+998900000000")
    course = Course.objects.create(name="English", monthly_price=400000)
    group = Group.objects.create(name="ENG-1", course=course, teacher=teacher, start_date=date(2026, 1, 1))
    room = Room.objects.create(name="101")
    student = Student.objects.create(first_name="Ali", last_name="Valiyev", phone="+998901112233")
    return {"teacher": teacher, "course": course, "group": group, "room": room, "student": student}


def rejected(model, **create):
    """Создание обязано упасть на уровне БД; транзакция теста при этом не ломается."""
    with pytest.raises(IntegrityError), transaction.atomic():
        model.objects.create(**create)


def ledger(base, kind, amount):
    return {"student": base["student"], "group": base["group"], "kind": kind, "amount": amount, "entry_date": date(2026, 10, 1)}


@pytest.mark.parametrize("kind,amount", [("charge", 100), ("charge", 0), ("payment", -5), ("payment", 0), ("excused_credit", -1)])
def test_ledger_sign_by_kind_is_enforced(base, kind, amount):
    rejected(LedgerEntry, **ledger(base, kind, amount))


@pytest.mark.parametrize("kind,amount", [("charge", -100), ("payment", 5), ("excused_credit", 1), ("adjustment", -7), ("adjustment", 7)])
def test_valid_ledger_entries_are_accepted(base, kind, amount):
    assert LedgerEntry.objects.create(**ledger(base, kind, amount)).pk


def test_group_constraints(base):
    common = {"name": "X", "course": base["course"], "teacher": base["teacher"], "start_date": date(2026, 1, 1)}
    rejected(Group, **common, max_students=0)
    rejected(Group, **common, teacher_percent=101)
    rejected(Group, **common, end_date=date(2025, 12, 31))
    assert Group.objects.create(**common, end_date=date(2026, 12, 31)).pk


def test_enrollment_end_not_before_start(base):
    rejected(Enrollment, student=base["student"], group=base["group"], status="finished", start_date=date(2026, 5, 1), end_date=date(2026, 4, 1))


def test_time_range_constraints(base):
    rejected(ScheduleSlot, group=base["group"], weekday=0, start_time=time(12), end_time=time(10), room=base["room"])
    rejected(ScheduleSlot, group=base["group"], weekday=0, start_time=time(10), end_time=time(10), room=base["room"])
    rejected(Lesson, group=base["group"], date=date(2026, 3, 1), start_time=time(12), end_time=time(11), room=base["room"], teacher=base["teacher"])


def test_room_capacity_and_course_price(base):
    rejected(Room, name="Zero", capacity=0)
    rejected(Course, name="Neg", monthly_price=-1)
    assert Room.objects.create(name="Free", capacity=None).pk and Course.objects.create(name="Free course", monthly_price=0).pk


def test_expense_amount_must_be_positive(base, director_user):
    category = ExpenseCategory.objects.create(name="Ijara", category_type="expense")
    rejected(ExpenseItem, category=category, amount=0, entry_date=date(2026, 10, 1), created_by=director_user)


# ---------- формы: ошибка пользователю, а не 500 ----------
def test_course_form_rejects_negative_price(client, director_user):
    client.force_login(director_user)
    response = client.post(reverse("courses:course_create"), {"name": "Neg", "level": "", "monthly_price": "-5", "is_active": "on"})
    assert response.status_code == 200 and not Course.objects.filter(name="Neg").exists()


def test_room_form_rejects_zero_capacity(client, admin_user):
    client.force_login(admin_user)
    response = client.post(reverse("schedule:room_create"), {"name": "Zero", "capacity": "0"})
    assert response.status_code == 200 and not Room.objects.filter(name="Zero").exists()
