"""F-07/F-10/F-12: границы суммы и даты платежа, проверка членства, безопасный разбор данных в финансовых формах."""

from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.courses.models import Course
from apps.finance.forms import MAX_PAYMENT_AMOUNT
from apps.finance.models import ExpenseCategory, ExpenseItem, LedgerEntry
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


def body(world, **extra):
    data = {"student_id": world["student"].pk, "group_id": world["group"].pk, "amount": "100000",
            "method": "cash", "entry_date": timezone.localdate().isoformat(), "comment": ""}
    data.update(extra)
    return data


def payments():
    return LedgerEntry.objects.filter(kind="payment").count()


@pytest.mark.parametrize("route", ["finance:payment_add", "finance:quick_payment"])
@pytest.mark.parametrize("amount", ["99999999999999999999", str(MAX_PAYMENT_AMOUNT + 1), "9223372036854775807"])
def test_absurd_amount_is_rejected_not_a_500(client, director_user, world, route, amount):
    client.force_login(director_user)
    response = client.post(reverse(route), body(world, amount=amount))
    assert response.status_code == 302
    assert payments() == 0


@pytest.mark.parametrize("route", ["finance:payment_add", "finance:quick_payment"])
def test_maximum_allowed_amount_is_accepted(client, director_user, world, route):
    client.force_login(director_user)
    client.post(reverse(route), body(world, amount=str(MAX_PAYMENT_AMOUNT)))
    assert payments() == 1


@pytest.mark.parametrize("route", ["finance:payment_add", "finance:quick_payment"])
def test_future_dated_payment_is_rejected(client, director_user, world, route):
    client.force_login(director_user)
    tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
    client.post(reverse(route), body(world, entry_date=tomorrow))
    client.post(reverse(route), body(world, entry_date="2099-01-01"))
    assert payments() == 0
    client.post(reverse(route), body(world, entry_date=timezone.localdate().isoformat()))
    assert payments() == 1  # сегодняшняя дата проходит


def test_payment_create_view_requires_enrollment_in_group(client, director_user, world):
    client.force_login(director_user)
    url = reverse("finance:payment_create", args=[world["student"].pk, world["other"].pk])
    client.post(url, {"amount": "5000", "method": "cash", "entry_date": timezone.localdate().isoformat()})
    assert payments() == 0
    own = reverse("finance:payment_create", args=[world["student"].pk, world["group"].pk])
    client.post(own, {"amount": "5000", "method": "cash", "entry_date": timezone.localdate().isoformat()})
    assert payments() == 1


def test_payment_form_default_date_is_local_today():
    from apps.finance.forms import PaymentForm

    assert PaymentForm().fields["entry_date"].initial == timezone.localdate()


def test_payroll_close_with_garbage_does_not_crash(client, director_user):
    client.force_login(director_user)
    response = client.post(reverse("finance:payroll_close"), {"year": "x", "month": "99"})
    assert response.status_code == 302


@pytest.mark.parametrize("data", [{"amount": ""}, {"amount": "abc"}, {"amount": "-5"}, {"amount": "1e3"}, {"amount": "0"}])
def test_expense_create_garbage_never_500(client, director_user, data):
    category = ExpenseCategory.objects.create(name="Ijara", category_type="expense")
    client.force_login(director_user)
    payload = {"category_id": category.pk, "amount": "5000", "entry_date": "2026-10-01"}
    payload.update(data)
    response = client.post(reverse("finance:expense_create"), payload)
    assert response.status_code == 302
    assert not ExpenseItem.objects.exists()


def test_expense_create_unknown_category_is_404(client, director_user):
    client.force_login(director_user)
    response = client.post(reverse("finance:expense_create"), {"category_id": "zzz", "amount": "10"})
    assert response.status_code == 404


def test_expense_create_valid_and_bad_date_falls_back_to_today(client, director_user):
    category = ExpenseCategory.objects.create(name="Ijara", category_type="expense")
    client.force_login(director_user)
    client.post(reverse("finance:expense_create"), {"category_id": category.pk, "amount": "7000", "entry_date": "not-a-date"})
    item = ExpenseItem.objects.get()
    assert item.amount == 7000 and item.entry_date == timezone.localdate()
