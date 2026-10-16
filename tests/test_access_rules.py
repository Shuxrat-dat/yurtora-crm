"""Regression tests for approved access rules.

- Finance and payroll (FOT): director only.
- Administrator: accepts payments and issues receipts, no finance reports,
  cannot change teacher salary or percent.
- Teacher: no financial data in context, no student phone numbers.
"""

from datetime import date

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.urls import reverse

from apps.courses.models import Course
from apps.finance.models import LedgerEntry
from apps.groups.forms import GroupForm
from apps.groups.models import Enrollment, Group
from apps.students.models import Student
from apps.teachers.forms import TeacherForm
from apps.teachers.models import Teacher

STUDENT_PHONE = "+998901234567"


@pytest.fixture
def school(db, teacher_user):
    teacher = Teacher.objects.create(
        user=teacher_user,
        full_name="Teacher One",
        phone="+998900000001",
        pay_type=Teacher.PayType.PERCENT,
    )
    course = Course.objects.create(name="English", level="A1", monthly_price=400000)
    group = Group.objects.create(
        name="G1",
        course=course,
        teacher=teacher,
        start_date=date(2026, 1, 1),
        teacher_percent=30,
    )
    student = Student.objects.create(
        first_name="Ali", last_name="Aliyev", phone=STUDENT_PHONE
    )
    Enrollment.objects.create(
        student=student,
        group=group,
        status=Enrollment.Status.ACTIVE,
        start_date=date(2026, 1, 1),
        paid_from=date(2026, 1, 1),
    )
    LedgerEntry.objects.create(
        student=student,
        group=group,
        kind=LedgerEntry.Kind.PAYMENT,
        amount=100000,
        method=LedgerEntry.PaymentMethod.CASH,
        entry_date=date(2026, 1, 5),
    )
    return {"teacher": teacher, "group": group, "student": student}


@pytest.mark.django_db
class TestFinanceAccess:
    def test_finance_dashboard_director_only(self, client, director_user, admin_user, teacher_user):
        url = reverse("finance:dashboard")
        client.force_login(director_user)
        assert client.get(url).status_code == 200
        client.force_login(admin_user)
        assert client.get(url).status_code == 403
        client.force_login(teacher_user)
        assert client.get(url).status_code == 403

    def test_payroll_and_my_salary_director_only(self, client, director_user, admin_user, teacher_user):
        client.force_login(admin_user)
        assert client.get(reverse("finance:payroll")).status_code == 403
        client.force_login(teacher_user)
        assert client.get(reverse("finance:payroll")).status_code == 403
        assert client.get(reverse("finance:my_salary")).status_code == 403
        client.force_login(director_user)
        assert client.get(reverse("finance:payroll")).status_code == 200

    def test_payment_accepted_by_administrator(self, client, admin_user, school):
        """Administrator may accept a payment (redirect after success, no 403)."""
        client.force_login(admin_user)
        url = reverse(
            "finance:payment_create",
            args=[school["student"].pk, school["group"].pk],
        )
        response = client.post(
            url,
            {"amount": 50000, "method": "cash", "entry_date": "2026-01-10"},
        )
        assert response.status_code == 302
        assert LedgerEntry.objects.filter(
            student=school["student"], kind=LedgerEntry.Kind.PAYMENT, amount=50000
        ).exists()

    def test_excused_credit_director_only(self, client, admin_user, school):
        charge = LedgerEntry.objects.create(
            student=school["student"],
            group=school["group"],
            kind=LedgerEntry.Kind.CHARGE,
            amount=-400000,
            entry_date=date(2026, 1, 6),
        )
        client.force_login(admin_user)
        response = client.post(
            reverse("finance:excused_credit", args=[charge.pk]),
            {"comment": "sabab"},
        )
        assert response.status_code == 403
        assert not LedgerEntry.objects.filter(reverses=charge).exists()


@pytest.mark.django_db
class TestTeacherCannotSeeFinance:
    def test_student_detail_has_no_finance_context(self, client, teacher_user, school):
        client.force_login(teacher_user)
        response = client.get(reverse("students:student_detail", args=[school["student"].pk]))
        assert response.status_code == 200
        assert response.context["payment_form"] is None
        assert response.context["ledger_entries"] == []
        assert response.context["enrollments_with_balance"] == []

    def test_student_detail_hides_phone_from_teacher(self, client, teacher_user, school):
        client.force_login(teacher_user)
        response = client.get(reverse("students:student_detail", args=[school["student"].pk]))
        assert STUDENT_PHONE not in response.content.decode()

    def test_student_list_search_by_phone_ignored_for_teacher(self, client, teacher_user, school):
        client.force_login(teacher_user)
        response = client.get(reverse("students:student_list"), {"q": STUDENT_PHONE})
        assert response.status_code == 200
        assert len(response.context["students"]) == 0

    def test_salary_page_forbidden_for_teacher(self, client, teacher_user, school):
        client.force_login(teacher_user)
        response = client.get(
            reverse("finance:my_salary"), {"teacher_id": school["teacher"].pk}
        )
        assert response.status_code == 403


@pytest.mark.django_db
class TestSalaryFieldsDirectorOnly:
    def test_teacher_form_hides_salary_fields_from_administrator(self, admin_user, director_user):
        admin_form = TeacherForm(user=admin_user)
        assert "monthly_salary" not in admin_form.fields
        assert "pay_type" not in admin_form.fields
        director_form = TeacherForm(user=director_user)
        assert "monthly_salary" in director_form.fields
        assert "pay_type" in director_form.fields

    def test_group_form_hides_teacher_percent_from_administrator(self, admin_user, director_user):
        assert "teacher_percent" not in GroupForm(user=admin_user).fields
        assert "teacher_percent" in GroupForm(user=director_user).fields


@pytest.mark.django_db
class TestClosePayrollRedirect:
    def test_close_month_redirects_back_to_payroll(self, client, director_user, school):
        client.force_login(director_user)
        response = client.post(reverse("finance:payroll_close"), {"year": 2026, "month": 8})
        assert response.status_code == 302
        assert response["Location"].startswith(
            reverse("finance:payroll") + "?year=2026&month=8"
        )


@pytest.mark.django_db
class TestSeedDemoGuard:
    @override_settings(DEBUG=False)
    def test_seed_refuses_outside_debug(self):
        with pytest.raises(CommandError):
            call_command("seed_demo")
