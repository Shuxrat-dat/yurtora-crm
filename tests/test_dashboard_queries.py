"""PERF-01: число запросов панели финансов и расчёта зарплат не растёт с числом учителей и групп; результаты верны."""

import json
from datetime import date

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.courses.models import Course
from apps.finance import services as finance
from apps.finance.models import ExpenseCategory, ExpenseItem, PayrollMonth, SalaryPayout
from apps.groups import services as groups
from apps.groups.models import Group
from apps.students.models import Student
from apps.teachers.models import Teacher

pytestmark = pytest.mark.django_db


def build(n_teachers, n_groups, director):
    course = Course.objects.get_or_create(name="C", defaults={"monthly_price": 800000})[0]
    start = Teacher.objects.count()
    teachers = [
        Teacher.objects.create(full_name=f"T{start + i}", phone=f"+99890000{start + i:04d}", pay_type="percent")
        for i in range(n_teachers)
    ]
    made = []
    for i in range(n_groups):
        g = Group.objects.create(name=f"G{Group.objects.count()}", course=course, teacher=teachers[i % n_teachers], start_date=date(2026, 1, 1), teacher_percent=30, max_students=50)
        s = Student.objects.create(first_name=f"S{Student.objects.count()}", last_name="P", phone=f"+99890100{Student.objects.count():04d}")
        groups.enroll_student(s, g, start_date=date(2026, 1, 1), user=director)
        finance.register_payment(s, g, 300000, "cash", None, date(2026, 10, 10))
        made.append(g)
    return made


def queries_for(client, url, params=None):
    with CaptureQueriesContext(connection) as ctx:
        response = client.get(url, params or {})
    assert response.status_code == 200
    return len(ctx)


def test_dashboard_query_count_does_not_grow_with_groups_and_teachers(client, director_user):
    client.force_login(director_user)
    build(2, 4, director_user)
    small = queries_for(client, reverse("finance:dashboard"), {"year": 2026, "month": 10})
    build(10, 40, director_user)
    large = queries_for(client, reverse("finance:dashboard"), {"year": 2026, "month": 10})
    assert large <= small + 6, f"запросов было {small}, стало {large}: появились запросы в цикле"
    assert large < 160


def test_compute_payroll_uses_a_constant_number_of_queries(director_user):
    build(2, 4, director_user)
    with CaptureQueriesContext(connection) as small:
        finance.compute_payroll(2026, 10)
    build(10, 40, director_user)
    with CaptureQueriesContext(connection) as large:
        finance.compute_payroll(2026, 10)
    assert len(large) == len(small) <= 8


def test_compute_payroll_values(director_user):
    made = build(2, 4, director_user)
    result = finance.compute_payroll(2026, 10)
    for teacher_data in result.values():
        for row in teacher_data["groups"]:
            assert row["base"] == 300000 and row["percent"] == 30 and row["amount"] == 90000
    assert sum(len(d["groups"]) for d in result.values()) == len(made)
    assert sum(d["total_accrued"] for d in result.values()) == 90000 * len(made)


def test_dashboard_year_chart_matches_the_data(client, director_user):
    made = build(1, 2, director_user)
    category = ExpenseCategory.objects.create(name="Ijara", category_type="expense")
    ExpenseItem.objects.create(category=category, amount=100000, entry_date=date(2026, 10, 3), created_by=director_user)
    ExpenseItem.objects.create(category=category, amount=50000, entry_date=date(2026, 5, 3), created_by=director_user)
    may = PayrollMonth.objects.create(year=2026, month=5)
    SalaryPayout.objects.create(teacher=made[0].teacher, month=may, amount=200000, method="cash", paid_date=date(2026, 5, 20), created_by=director_user)
    client.force_login(director_user)
    response = client.get(reverse("finance:dashboard"), {"year": 2026, "month": 10})
    income = json.loads(response.context["chart_income_json"])
    expenses = json.loads(response.context["chart_expenses_json"])
    assert income[9] == 600000 and sum(income) == 600000  # октябрь: 2 платежа по 300 000
    assert expenses[9] == 100000 and expenses[4] == 250000  # май: 50 000 расход + 200 000 выплата


def test_inactive_teacher_with_payments_stays_in_open_month_payroll(director_user):
    """F-09: ушедший учитель не должен исчезать из расчёта, пока по его группам есть платежи месяца."""
    made = build(2, 2, director_user)
    gone = made[0].teacher
    Teacher.objects.filter(pk=gone.pk).update(is_active=False)
    result = finance.compute_payroll(2026, 10)
    assert gone.pk in result and result[gone.pk]["total_accrued"] == 90000


def test_inactive_teacher_without_activity_is_not_listed(director_user):
    build(1, 1, director_user)
    idle = Teacher.objects.create(full_name="Idle", phone="+998900009999", is_active=False)
    assert idle.pk not in finance.compute_payroll(2026, 10)


def test_inactive_teacher_with_payout_stays_listed(director_user):
    made = build(1, 1, director_user)
    teacher = made[0].teacher
    month = PayrollMonth.objects.create(year=2026, month=9)
    SalaryPayout.objects.create(teacher=teacher, month=month, amount=100000, method="cash", paid_date=date(2026, 9, 20), created_by=director_user)
    Teacher.objects.filter(pk=teacher.pk).update(is_active=False)
    result = finance.compute_payroll(2026, 9)
    assert teacher.pk in result and result[teacher.pk]["total_paid"] == 100000
