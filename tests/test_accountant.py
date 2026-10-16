"""Buxgalter roli: to'lov, chek, qarzdorlar, kunlik kassa va o'quvchilarni ko'rish; boshqa hamma narsa yopiq."""

from datetime import date

import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.courses.models import Course
from apps.finance.models import LedgerEntry
from apps.groups.models import Enrollment, Group
from apps.students.models import Student
from apps.teachers.models import Teacher

pytestmark = pytest.mark.django_db


@pytest.fixture
def accountant(password):
    return User.objects.create_user(username="buxgalter", password=password, role=User.Role.ACCOUNTANT)


@pytest.fixture
def world():
    teacher = Teacher.objects.create(full_name="Ustoz", phone="+998900000000")
    course = Course.objects.create(name="English", monthly_price=400000)
    group = Group.objects.create(name="ENG-1", course=course, teacher=teacher, start_date=date(2026, 1, 1), max_students=20)
    student = Student.objects.create(first_name="Ali", last_name="Valiyev", phone="+998901112233")
    Enrollment.objects.create(student=student, group=group, status="active", start_date=date(2026, 3, 1), paid_from=date(2026, 3, 1))
    return {"group": group, "student": student}


def test_accountant_can_use_finance_and_read_students(client, accountant, world):
    client.force_login(accountant)
    allowed = [
        reverse("finance:daily_cash"),
        reverse("finance:debtors_list"),
        reverse("students:student_list"),
        reverse("students:student_detail", args=[world["student"].pk]),
    ]
    for url in allowed:
        assert client.get(url).status_code == 200, url


def test_accountant_denied_everything_else(client, accountant, world):
    client.force_login(accountant)
    student, group = world["student"], world["group"]
    denied = [
        reverse("finance:dashboard"),
        reverse("finance:payroll"),
        reverse("finance:my_salary"),
        reverse("finance:refund", args=[student.pk, group.pk]),
        reverse("finance:receipt_settings"),
        reverse("finance:quick_payment"),
        reverse("core:audit_log"),
        reverse("accounts:staff_list"),
        reverse("students:student_create"),
        reverse("students:student_edit", args=[student.pk]),
        reverse("groups:group_list"),
        reverse("groups:group_transfer", args=[group.pk, student.pk]),
        reverse("teachers:teacher_list"),
        reverse("courses:course_list"),
    ]
    for url in denied:
        assert client.get(url).status_code in (403, 404), url


def test_accountant_registers_payment_but_cannot_edit_student(client, accountant, world):
    client.force_login(accountant)
    student, group = world["student"], world["group"]
    response = client.post(
        reverse("finance:payment_create", args=[student.pk, group.pk]),
        {"amount": "100000", "method": "cash", "entry_date": "2026-03-10"},
    )
    assert response.status_code == 302
    assert LedgerEntry.objects.filter(kind="payment", amount=100000, created_by=accountant).exists()
    edit = client.post(reverse("students:student_edit", args=[student.pk]), {"first_name": "X"})
    assert edit.status_code == 403


def test_accountant_home_goes_to_daily_cash(client, accountant):
    client.force_login(accountant)
    response = client.get(reverse("accounts:home"))
    assert response.status_code == 302 and response["Location"] == reverse("finance:daily_cash")


def test_student_card_hides_edit_actions_for_accountant(client, accountant, world):
    client.force_login(accountant)
    html = client.get(reverse("students:student_detail", args=[world["student"].pk])).content.decode()
    assert 'id="enroll-modal"' not in html and "Vasiy qo'shish" not in html
    assert "Pul qaytarish" not in html
