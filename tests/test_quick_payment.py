"""Быстрая оплата на панели финансов (F-03): раньше всегда падала из-за строки вместо даты."""

from datetime import date

import pytest
from django.urls import reverse

from apps.courses.models import Course
from apps.finance.models import LedgerEntry
from apps.groups.models import Enrollment, Group
from apps.students.models import Student
from apps.teachers.models import Teacher

pytestmark = pytest.mark.django_db


@pytest.fixture
def world():
    teacher = Teacher.objects.create(full_name="Ustoz", phone="+998900000000")
    course = Course.objects.create(name="English", monthly_price=400000)
    group = Group.objects.create(name="ENG-1", course=course, teacher=teacher, start_date="2026-01-01")
    other = Group.objects.create(name="ENG-2", course=course, teacher=teacher, start_date="2026-01-01")
    student = Student.objects.create(first_name="Ali", last_name="Valiyev", phone="+998901112233")
    Enrollment.objects.create(student=student, group=group, status="active", start_date=date(2026, 9, 1))
    return {"student": student, "group": group, "other": other}


def post(client, world, **overrides):
    data = {
        "student_id": world["student"].pk, "group_id": world["group"].pk, "amount": "150000",
        "method": "cash", "entry_date": "2026-10-10", "comment": "kvitansiya 7",
    }
    data.update(overrides)
    return client.post(reverse("finance:quick_payment"), data, follow=True)


def messages_of(response):
    return [str(m) for m in response.context["messages"]]


def test_quick_payment_saves_payment(client, director_user, world):
    client.force_login(director_user)
    response = post(client, world)
    entry = LedgerEntry.objects.get(kind="payment")
    assert (entry.amount, entry.method, entry.entry_date, entry.comment) == (150000, "cash", date(2026, 10, 10), "kvitansiya 7")
    assert entry.student == world["student"] and entry.group == world["group"] and entry.created_by == director_user
    assert any("to'lov qabul qilindi" in m for m in messages_of(response))


def test_quick_payment_rejects_group_without_enrollment(client, director_user, world):
    client.force_login(director_user)
    response = post(client, world, group_id=world["other"].pk)
    assert not LedgerEntry.objects.exists()
    assert any("a'zo emas" in m for m in messages_of(response))


@pytest.mark.parametrize("field,value", [("amount", "abc"), ("amount", "0"), ("amount", "-5"), ("amount", "1.5"), ("entry_date", ""), ("entry_date", "not-a-date"), ("method", "gold")])
def test_quick_payment_invalid_input_is_a_message_not_a_500(client, director_user, world, field, value):
    client.force_login(director_user)
    response = post(client, world, **{field: value})
    assert response.status_code == 200
    assert not LedgerEntry.objects.exists()
    assert any("noto'g'ri" in m for m in messages_of(response))


def test_quick_payment_director_only(client, admin_user, world):
    client.force_login(admin_user)
    assert post(client, world).status_code == 403
    assert not LedgerEntry.objects.exists()
