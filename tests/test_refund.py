"""Pul qaytarish: limitlar, rol, idempotentlik, sof tushum, CHECK cheklov."""

from datetime import date

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse

from apps.core.daily_stats import get_daily_stats
from apps.courses.models import Course
from apps.finance import services as finance
from apps.finance.models import LedgerEntry
from apps.groups.models import Enrollment, Group
from apps.students.models import Student
from apps.teachers.models import Teacher

pytestmark = pytest.mark.django_db
DAY = date(2026, 3, 10)


@pytest.fixture
def world():
    teacher = Teacher.objects.create(full_name="Ustoz", phone="+998900000000")
    course = Course.objects.create(name="English", monthly_price=400000)
    group = Group.objects.create(name="ENG-1", course=course, teacher=teacher, start_date=date(2026, 1, 1), max_students=20)
    student = Student.objects.create(first_name="Ali", last_name="Valiyev", phone="+998901112233")
    Enrollment.objects.create(student=student, group=group, status="active", start_date=date(2026, 3, 1), paid_from=date(2026, 3, 1))
    return {"group": group, "student": student}


def pay(world, user, amount=500000):
    return finance.register_payment(world["student"], world["group"], amount, "cash", user, DAY)


def refund(world, user, amount, comment="xato to'lov", key=None):
    return finance.register_refund(world["student"], world["group"], amount, "cash", user, DAY, comment, idempotency_key=key)


def test_refund_is_negative_and_reduces_balance(director_user, world):
    pay(world, director_user)
    entry = refund(world, director_user, 200000)
    assert entry.kind == "refund" and entry.amount == -200000
    assert finance.get_balance(world["student"], world["group"]) == 300000


def test_cannot_refund_more_than_overpayment(director_user, world):
    pay(world, director_user)
    with pytest.raises(ValidationError):
        refund(world, director_user, 500001)
    refund(world, director_user, 500000)
    with pytest.raises(ValidationError):
        refund(world, director_user, 1)


def test_cannot_refund_a_debtor(director_user, world):
    finance.add_adjustment(world["student"], world["group"], -100000, director_user, "qarz")
    with pytest.raises(ValidationError):
        refund(world, director_user, 1000)


def test_comment_required_and_director_only(director_user, admin_user, world):
    pay(world, director_user)
    with pytest.raises(ValidationError):
        refund(world, director_user, 1000, comment="  ")
    with pytest.raises(PermissionDenied):
        refund(world, admin_user, 1000)


def test_refund_is_idempotent(director_user, world):
    pay(world, director_user)
    refund(world, director_user, 1000, key="k-1")
    with pytest.raises(finance.DuplicatePayment):
        refund(world, director_user, 1000, key="k-1")
    assert LedgerEntry.objects.filter(kind="refund").count() == 1


def test_net_income_subtracts_refunds(director_user, world):
    pay(world, director_user)
    refund(world, director_user, 200000)
    assert get_daily_stats(director_user, DAY)["finance"]["total"] == 300000


def test_check_constraint_rejects_positive_refund(world):
    with pytest.raises(IntegrityError), transaction.atomic():
        LedgerEntry.objects.create(student=world["student"], group=world["group"], kind="refund", amount=100, entry_date=DAY)


def test_refund_view_access(client, director_user, admin_user, world):
    pay(world, director_user)
    url = reverse("finance:refund", args=[world["student"].pk, world["group"].pk])
    client.force_login(admin_user)
    assert client.get(url).status_code == 403
    client.force_login(director_user)
    assert client.get(url).status_code == 200
    response = client.post(url, {"amount": "100000", "method": "cash", "entry_date": "2026-03-10", "comment": "sabab"})
    assert response.status_code == 302
    assert LedgerEntry.objects.filter(kind="refund", amount=-100000).count() == 1
