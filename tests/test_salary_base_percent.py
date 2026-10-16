"""O'qituvchi profilidagi asosiy foiz: guruhning alohida foizi ustun, bo'sh foiz profildan, 0% — aniq qiymat."""

import html
from datetime import date

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.courses.models import Course
from apps.finance import services as finance
from apps.finance.models import PayrollLine
from apps.groups.models import (
    PERCENT_FROM_GROUP,
    PERCENT_FROM_TEACHER,
    Enrollment,
    Group,
)
from apps.students.models import Student
from apps.teachers.models import Teacher

pytestmark = pytest.mark.django_db
YEAR, MONTH, DAY = 2026, 3, date(2026, 3, 10)


@pytest.fixture
def world(director_user):
    course = Course.objects.create(name="English", monthly_price=1_000_000)
    teacher = Teacher.objects.create(full_name="Ustoz", phone="+998900000000", pay_type="percent", base_percent=40)
    n = {"i": 0}

    def group(name, percent=None, paid=1_000_000, teacher_obj=None):
        g = Group.objects.create(
            name=name, course=course, teacher=teacher_obj or teacher, start_date=date(2026, 1, 1), teacher_percent=percent
        )
        if paid:
            n["i"] += 1
            s = Student.objects.create(first_name=f"S{n['i']}", last_name="Probe", phone=f"+99890000{n['i']:04d}")
            Enrollment.objects.create(student=s, group=g, status="active", start_date=date(2026, 3, 1), paid_from=date(2026, 3, 1))
            finance.register_payment(s, g, paid, "cash", director_user, DAY)
        return g

    return {"teacher": teacher, "group": group, "course": course, "director": director_user}


def rows(teacher):
    data = finance.compute_payroll(YEAR, MONTH)[teacher.pk]
    return data, {r["group"].name: r for r in data["groups"]}


# 1-2. group percent wins; empty group percent falls back to the teacher's base percent
def test_group_percent_wins_and_empty_uses_base(world):
    a = world["group"]("A")
    b = world["group"]("B", percent=50)
    assert a.resolve_teacher_percent() == (40, PERCENT_FROM_TEACHER)
    assert b.resolve_teacher_percent() == (50, PERCENT_FROM_GROUP)
    data, by = rows(world["teacher"])
    assert (by["A"]["percent"], by["A"]["source"], by["A"]["amount"]) == (40, "teacher", 400_000)
    assert (by["B"]["percent"], by["B"]["source"], by["B"]["amount"]) == (50, "group", 500_000)
    assert data["total_accrued"] == 900_000


# 3. different individual percents across one teacher's groups (example 2 from the task)
def test_mixed_individual_percents(world):
    world["group"]("A")
    world["group"]("B", percent=50)
    world["group"]("C", percent=35)
    _, by = rows(world["teacher"])
    assert {k: v["percent"] for k, v in by.items()} == {"A": 40, "B": 50, "C": 35}


# 4. changing the base percent affects only groups without an individual percent, and never rewrites them
def test_base_change_only_affects_inheriting_groups(world):
    a = world["group"]("A")
    b = world["group"]("B", percent=50)
    teacher = world["teacher"]
    teacher.base_percent = 45
    teacher.save(update_fields=["base_percent"])
    _, by = rows(teacher)
    assert by["A"]["percent"] == 45 and by["B"]["percent"] == 50
    a.refresh_from_db()
    b.refresh_from_db()
    assert a.teacher_percent is None and b.teacher_percent == 50


# 5. explicit 0% in a group is an individual setting, not replaced by the base percent
def test_zero_group_percent_is_not_replaced(world):
    z = world["group"]("Z", percent=0)
    assert z.resolve_teacher_percent() == (0, PERCENT_FROM_GROUP)
    _, by = rows(world["teacher"])
    assert (by["Z"]["percent"], by["Z"]["amount"], by["Z"]["missing"]) == (0, 0, False)


# 6. neither percent set: warning, no accrual by an invented rate, month cannot be closed
def test_missing_percent_warns_and_blocks_close(client, world):
    teacher = world["teacher"]
    teacher.base_percent = None
    teacher.save(update_fields=["base_percent"])
    world["group"]("N")
    data, by = rows(teacher)
    assert by["N"]["missing"] and by["N"]["percent"] is None and by["N"]["amount"] == 0
    assert data["total_accrued"] == 0 and len(data["missing_percent"]) == 1

    client.force_login(world["director"])
    html = client.get(reverse("finance:payroll") + f"?year={YEAR}&month={MONTH}").content.decode()
    assert "Ish haqi foizi sozlanmagan guruhlar bor" in html and "Foiz sozlanmagan" in html

    with pytest.raises(ValidationError, match="foizi sozlanmagan"):
        finance.close_payroll_month(YEAR, MONTH, world["director"])
    assert not PayrollLine.objects.exists()


def test_missing_percent_without_payments_does_not_block_close(world):
    teacher = world["teacher"]
    teacher.base_percent = None
    teacher.save(update_fields=["base_percent"])
    world["group"]("Empty", paid=0)
    world["group"]("Paid", percent=30)
    finance.close_payroll_month(YEAR, MONTH, world["director"])
    assert list(PayrollLine.objects.values_list("group__name", "percent", "amount")) == [("Paid", 30, 300_000)]


# 7. closing freezes the resolved percent; later base changes don't touch the closed month; reports still work
def test_closed_month_freezes_inherited_percent(client, world):
    world["group"]("A")
    finance.close_payroll_month(YEAR, MONTH, world["director"])
    line = PayrollLine.objects.get()
    assert (line.percent, line.amount) == (40, 400_000)
    teacher = world["teacher"]
    teacher.base_percent = 60
    teacher.save(update_fields=["base_percent"])
    _, by = rows(teacher)
    assert (by["A"]["percent"], by["A"]["source"], by["A"]["amount"]) == (40, "closed", 400_000)

    client.force_login(world["director"])
    assert client.get(reverse("finance:dashboard") + f"?year={YEAR}&month={MONTH}").status_code == 200
    csv_text = client.get(reverse("finance:payroll_export") + f"?year={YEAR}&month={MONTH}").content.decode("utf-8-sig")
    assert "40%" in csv_text


def test_fixed_salary_teacher_unaffected(world):
    fixed = Teacher.objects.create(full_name="Oklad", phone="+998900000009", pay_type="fixed", monthly_salary=3_000_000)
    world["group"]("F", teacher_obj=fixed)
    data = finance.compute_payroll(YEAR, MONTH)[fixed.pk]
    assert data["total_accrued"] == 3_000_000 and data["missing_percent"] == []


# 8. boundaries and invalid input in the teacher and group forms
@pytest.mark.parametrize("value, ok", [("0", True), ("100", True), ("", True), ("-1", False), ("101", False), ("abc", False), ("12.5", False)])
def test_teacher_base_percent_validation(client, world, value, ok):
    teacher = world["teacher"]
    client.force_login(world["director"])
    payload = {"full_name": "Ustoz", "phone": "+998900000000", "user": "", "pay_type": "percent", "base_percent": value, "is_active": "on"}
    response = client.post(reverse("teachers:teacher_edit", args=[teacher.pk]), payload)
    assert (response.status_code == 302) is ok
    teacher.refresh_from_db()
    if ok:
        assert teacher.base_percent == (int(value) if value else None)
    else:
        assert teacher.base_percent == 40


@pytest.mark.parametrize("value, ok", [("0", True), ("100", True), ("", True), ("-5", False), ("150", False), ("x", False)])
def test_group_percent_validation(client, world, value, ok):
    group = world["group"]("G", percent=25, paid=0)
    client.force_login(world["director"])
    payload = {"name": "G", "course": world["course"].pk, "teacher": world["teacher"].pk, "max_students": 15,
               "start_date": "2026-01-01", "end_date": "", "status": "active", "teacher_percent": value}
    response = client.post(reverse("groups:group_edit", args=[group.pk]), payload)
    assert (response.status_code == 302) is ok
    group.refresh_from_db()
    assert group.teacher_percent == ((int(value) if value else None) if ok else 25)


def test_admin_edit_keeps_group_percent(client, admin_user, world):
    """Administrator foiz maydonini ko'rmaydi: guruhni tahrirlashi foizni o'chirib yubormasligi kerak."""
    group = world["group"]("G", percent=55, paid=0)
    client.force_login(admin_user)
    payload = {"name": "G2", "course": world["course"].pk, "teacher": world["teacher"].pk, "max_students": 15,
               "start_date": "2026-01-01", "end_date": "", "status": "active"}
    assert client.post(reverse("groups:group_edit", args=[group.pk]), payload).status_code == 302
    group.refresh_from_db()
    assert group.name == "G2" and group.teacher_percent == 55


def test_screens_show_percent_source(client, world):
    a = world["group"]("A", paid=0)
    world["group"]("B", percent=50, paid=0)
    client.force_login(world["director"])
    detail = client.get(reverse("groups:group_detail", args=[a.pk])).content.decode()
    assert "40%" in detail and "Profildan" in detail
    listing = client.get(reverse("teachers:teacher_list")).content.decode()
    assert "40–50%" in listing
    form = client.get(reverse("groups:group_edit", args=[a.pk])).content.decode()
    form = html.unescape(form)
    assert "teacher-base-percents" in form and "o'qituvchi profilidagi asosiy foiz ishlatiladi" in form
