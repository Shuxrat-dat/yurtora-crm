"""Platej qaysi oy uchun, qarz qaysi oyda: oylik hisob, to'lov oynasi, qarzdorlar ro'yxati, chek."""

from datetime import date

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.core.months import add_months, parse_month
from apps.courses.models import Course
from apps.finance import services
from apps.finance.models import LedgerEntry
from apps.groups.models import Enrollment, Group
from apps.students.models import Student
from apps.teachers.models import Teacher

pytestmark = pytest.mark.django_db

SEP, OCT, NOV = date(2026, 9, 1), date(2026, 10, 1), date(2026, 11, 1)


@pytest.fixture
def world(director_user):
    teacher = Teacher.objects.create(full_name="Ustoz", phone="+998900000070")
    course = Course.objects.create(name="Matematika", monthly_price=350000)
    group = Group.objects.create(name="Mat-1", course=course, teacher=teacher, start_date="2026-09-01")
    student = Student.objects.create(first_name="Aziz", last_name="Talaba", phone="")
    enrollment = Enrollment.objects.create(student=student, group=group, status="active", start_date=date(2026, 9, 1))
    return {"student": student, "group": group, "enrollment": enrollment, "user": director_user}


def charge(w, amount, when):
    return LedgerEntry.objects.create(student=w["student"], group=w["group"], enrollment=w["enrollment"], kind="charge", amount=-amount, entry_date=when, created_by=w["user"])


def pay(w, amount, when, period=None):
    return services.register_payment(w["student"], w["group"], amount, "cash", w["user"], when, period=period)


def test_payment_defaults_to_month_of_its_date_and_can_be_set_explicitly(world):
    assert pay(world, 100000, date(2026, 10, 17)).period == OCT
    assert pay(world, 100000, date(2026, 10, 17), period=date(2026, 9, 20)).period == SEP  # kun e'tiborga olinmaydi, oy 1-sanaga keltiriladi
    assert pay(world, 100000, date(2026, 10, 17), period=NOV).period == NOV  # oldindan to'lov


def test_far_away_month_rejected(world):
    with pytest.raises(ValidationError):
        pay(world, 100000, date(2026, 10, 17), period=add_months(OCT, -25))
    with pytest.raises(ValidationError):
        pay(world, 100000, date(2026, 10, 17), period=add_months(OCT, 13))


def test_breakdown_shows_debt_month_and_paid_month_separately(world):
    # Sentabrda 350 000 hisoblandi, to'lanmadi. Oktabrda 350 000 hisoblandi va to'liq to'landi.
    charge(world, 350000, date(2026, 9, 10))
    charge(world, 350000, date(2026, 10, 10))
    pay(world, 350000, date(2026, 10, 12), period=OCT)
    rows = services.monthly_breakdown(world["student"], world["group"])
    by = {r["value"]: r for r in rows}
    assert by["2026-09"]["balance"] == -350000 and by["2026-09"]["paid"] == 0
    assert by["2026-10"]["balance"] == 0 and by["2026-10"]["paid"] == 350000 and by["2026-10"]["charged"] == 350000
    # Oy balanslari yig'indisi umumiy balansga teng
    assert sum(r["balance"] for r in rows) == services.get_balance(world["student"], world["group"]) == -350000


def test_paying_the_old_month_clears_that_debt(world):
    charge(world, 350000, date(2026, 9, 10))
    pay(world, 350000, date(2026, 10, 12), period=SEP)
    rows = services.monthly_breakdown(world["student"], world["group"])
    assert [(r["value"], r["balance"]) for r in rows] == [("2026-09", 0)]


def test_advance_payment_shows_as_credit_in_future_month(world):
    pay(world, 350000, date(2026, 10, 12), period=NOV)
    [row] = services.monthly_breakdown(world["student"], world["group"])
    assert row["value"] == "2026-11" and row["balance"] == 350000


def test_excused_credit_goes_to_the_month_of_the_reversed_lesson(world):
    c = charge(world, 30000, date(2026, 9, 28))
    LedgerEntry.objects.create(student=world["student"], group=world["group"], enrollment=world["enrollment"], kind="excused_credit", amount=30000, entry_date=date(2026, 10, 2), reverses=c, created_by=world["user"])
    [row] = services.monthly_breakdown(world["student"], world["group"])
    assert row["value"] == "2026-09" and row["balance"] == 0 and row["other"] == 30000


def test_debt_months_map_lists_only_months_with_debt(world):
    charge(world, 350000, date(2026, 9, 10))
    charge(world, 350000, date(2026, 10, 10))
    pay(world, 350000, date(2026, 10, 12), period=OCT)
    result = services.debt_months_map([(world["student"].pk, world["group"].pk)])
    assert result[(world["student"].pk, world["group"].pk)] == [{"label": "Sentabr 2026", "value": "2026-09", "amount": 350000}]
    assert services.debt_months_map([]) == {}


def test_modal_form_offers_months_and_preselects_oldest_debt(client, world):
    charge(world, 350000, date(2026, 9, 10))
    client.force_login(world["user"])
    html = client.get(reverse("finance:payment_modal_form", args=[world["student"].pk])).content.decode()
    assert 'name="period"' in html and 'value="2026-09" selected' in html
    assert "Sentabr 2026: 350" in html  # qarz oylari eslatmasi
    assert 'data-debt-first="2026-09"' in html


def test_modal_saves_selected_month_and_receipt_shows_it(client, world):
    client.force_login(world["user"])
    response = client.post(reverse("finance:payment_add"), {
        "student_id": world["student"].pk, "group_id": world["group"].pk, "amount": "350 000", "method": "cash",
        "entry_date": "2026-10-17", "period": "2026-09", "idempotency_key": "k1", "action": "print",
    })
    assert response.status_code == 302
    entry = LedgerEntry.objects.get(kind="payment")
    assert entry.period == SEP
    receipt = client.get(reverse("finance:receipt", args=[entry.pk])).content.decode()
    assert "Qaysi oy uchun" in receipt and "Sentabr 2026" in receipt


def test_invalid_period_is_rejected_not_saved(client, world):
    client.force_login(world["user"])
    client.post(reverse("finance:payment_add"), {
        "student_id": world["student"].pk, "group_id": world["group"].pk, "amount": "100000", "method": "cash",
        "entry_date": "2026-10-17", "period": "abc", "idempotency_key": "k2",
    })
    assert not LedgerEntry.objects.filter(kind="payment").exists()


def test_student_card_shows_month_table_and_debtors_show_month_chips(client, world):
    charge(world, 350000, date(2026, 9, 10))
    charge(world, 350000, date(2026, 10, 10))
    pay(world, 350000, date(2026, 10, 12), period=OCT)
    client.force_login(world["user"])
    card = client.get(reverse("students:student_detail", args=[world["student"].pk])).content.decode()
    assert "Oylar bo&#x27;yicha" in card or "Oylar bo'yicha" in card
    assert "Sentabr 2026" in card and "Oktabr 2026" in card and 'name="period"' in card
    debtors = client.get(reverse("finance:debtors_list")).content.decode()
    assert "Qaysi oylar" in debtors and "Sentabr 2026: 350" in debtors and "Oktabr 2026:" not in debtors


def test_parse_month_helper():
    assert parse_month("2026-10") == OCT
    with pytest.raises(ValueError):
        parse_month("2026/10")
