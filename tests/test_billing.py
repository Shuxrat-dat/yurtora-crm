"""Obuna: holatlar, to'lov bilan uzaytirish, faqat ko'rish va bloklash, banner, tarif limitlari."""

import html
from datetime import date, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from django_tenants.utils import schema_context

from apps.accounts.models import User
from apps.students import importer
from apps.students.models import Student
from apps.tenants import billing
from apps.tenants.models import Center, Plan, SubscriptionPayment
from apps.tenants.services import active_center_schemas, create_center

pytestmark = pytest.mark.django_db
TODAY = timezone.localdate()
PASSWORD = "Strong-Pass-2026!"


def set_center(**fields):
    """Test markazi (connection.tenant) maydonlarini o'zgartiradi: CenterMiddleware ham shu yozuvni o'qiydi."""
    c = connection.tenant
    for key, value in fields.items():
        setattr(c, key, value)
    c.save()
    return c


def small_plan(**limits):
    return Plan.objects.create(code="tiny", name="Tiny", price_month=100_000, **limits)


def logged_in(user):
    client = Client(HTTP_HOST="testcenter.localhost")
    client.force_login(user)
    return client


def student_form(first, last, phone):
    return {"first_name": first, "last_name": last, "phone": phone, "status": "active", "joined_date": TODAY.isoformat()}


def text(response):
    return html.unescape(response.content.decode())


# --- status rules ---------------------------------------------------------------


@pytest.mark.parametrize(
    "trial, paid, expected",
    [
        (None, None, billing.ACTIVE),                      # muddat belgilanmagan (ichki markaz)
        (TODAY + timedelta(days=5), None, billing.TRIAL),
        (TODAY, None, billing.TRIAL),                      # oxirgi kun ham ruxsat
        (TODAY - timedelta(days=20), TODAY + timedelta(days=3), billing.ACTIVE),
        (TODAY - timedelta(days=1), None, billing.GRACE),
        (TODAY - timedelta(days=billing.GRACE_DAYS), None, billing.GRACE),
        (TODAY - timedelta(days=billing.GRACE_DAYS + 1), None, billing.READ_ONLY),
        (TODAY - timedelta(days=billing.GRACE_DAYS + billing.READ_ONLY_DAYS), None, billing.READ_ONLY),
        (TODAY - timedelta(days=billing.GRACE_DAYS + billing.READ_ONLY_DAYS + 1), None, billing.BLOCKED),
    ],
)
def test_subscription_status(trial, paid, expected):
    c = Center(schema_name="x", name="X", trial_ends_at=trial, paid_until=paid)
    assert billing.subscription(c, today=TODAY).status == expected


def test_manual_block_wins():
    c = Center(schema_name="x", name="X", paid_until=TODAY + timedelta(days=30), is_active=False)
    sub = billing.subscription(c, today=TODAY)
    assert sub.status == billing.BLOCKED and sub.manual_block


def test_add_months_clamps_to_month_end():
    assert billing.add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert billing.add_months(date(2028, 1, 31), 1) == date(2028, 2, 29)
    assert billing.add_months(date(2026, 11, 15), 3) == date(2027, 2, 15)


# --- payments --------------------------------------------------------------------


def test_payment_during_trial_keeps_trial_days():
    c = set_center(plan=Plan.objects.get(code="standart"), trial_ends_at=TODAY + timedelta(days=4))
    payment = billing.record_payment(c, months=1, amount=499_000)
    assert payment.period_start == TODAY + timedelta(days=5)
    assert c.paid_until == billing.add_months(payment.period_start, 1) - timedelta(days=1)


def test_payment_after_expiry_starts_today_and_unblocks():
    c = set_center(plan=Plan.objects.get(code="start"), trial_ends_at=TODAY - timedelta(days=30))
    assert billing.subscription(c).status == billing.BLOCKED
    payment = billing.record_payment(c, months=2, amount=598_000)
    assert payment.period_start == TODAY
    assert billing.subscription(Center.objects.get(pk=c.pk)).status == billing.ACTIVE


def test_consecutive_payments_extend_back_to_back():
    c = set_center(plan=Plan.objects.get(code="start"), trial_ends_at=None, paid_until=TODAY + timedelta(days=10))
    first_end = c.paid_until
    payment = billing.record_payment(c, months=1, amount=299_000)
    assert payment.period_start == first_end + timedelta(days=1)


def test_payment_requires_plan_and_positive_months():
    c = set_center(plan=None)
    with pytest.raises(ValidationError):
        billing.record_payment(c, months=1, amount=1)
    with pytest.raises(ValidationError):
        billing.record_payment(c, months=0, amount=1, plan=Plan.objects.get(code="start"))


# --- access by status --------------------------------------------------------------


def test_grace_keeps_full_access_with_warning(director_user):
    set_center(plan=Plan.objects.get(code="start"), trial_ends_at=TODAY - timedelta(days=1))
    client = logged_in(director_user)
    page = text(client.get(reverse("accounts:home")))
    assert "Obuna muddati" in page and "faqat ko'rish rejimi yoqiladi" in page
    response = client.post(reverse("students:student_create"), student_form("A", "B", "+998901234567"))
    assert response.status_code != 403  # yozish ruxsat (forma o'zi tekshiradi)


def test_read_only_allows_viewing_but_not_changes(director_user, password):
    set_center(plan=Plan.objects.get(code="start"), trial_ends_at=TODAY - timedelta(days=billing.GRACE_DAYS + 2))
    client = logged_in(director_user)
    assert client.get(reverse("students:student_list")).status_code == 200
    response = client.post(reverse("students:student_create"), student_form("A", "B", "+998901234567"))
    assert response.status_code == 403 and "Faqat ko'rish rejimi" in text(response)
    assert not Student.objects.exists()
    # tizimga kirish ishlaydi (obuna sahifasini ko'rish uchun)
    fresh = Client(HTTP_HOST="testcenter.localhost")
    assert fresh.post("/login/", {"username": director_user.username, "password": password}).status_code == 302
    assert client.get(reverse("billing:overview")).status_code == 200


def test_expired_center_is_blocked(director_user):
    set_center(plan=Plan.objects.get(code="start"), trial_ends_at=TODAY - timedelta(days=60))
    response = logged_in(director_user).get(reverse("accounts:home"))
    assert response.status_code == 403 and "obuna muddati" in text(response)


# --- banner and billing page ------------------------------------------------------


def test_trial_banner_for_director_only_near_the_end(director_user, teacher_user):
    set_center(plan=Plan.objects.get(code="standart"), trial_ends_at=TODAY + timedelta(days=3))
    assert "Sinov davri: 4 kun qoldi" in text(logged_in(director_user).get(reverse("accounts:home")))
    assert "Sinov davri" not in text(logged_in(teacher_user).get(reverse("accounts:home")))
    set_center(trial_ends_at=TODAY + timedelta(days=12))
    assert "Sinov davri:" not in text(logged_in(director_user).get(reverse("accounts:home")))


def test_billing_page_director_only(director_user, admin_user):
    set_center(plan=Plan.objects.get(code="start"), trial_ends_at=TODAY + timedelta(days=9))
    page = text(logged_in(director_user).get(reverse("billing:overview")))
    assert "Sinov davri" in page and "Start" in page and "Standart" in page and "/ 100" in page
    assert logged_in(admin_user).get(reverse("billing:overview")).status_code == 403


# --- plan limits ---------------------------------------------------------------------


def test_student_limit_on_create_and_unarchive(admin_user):
    set_center(plan=small_plan(max_students=1))
    Student.objects.create(first_name="Ali", last_name="Valiyev", phone="+998900000001")
    client = logged_in(admin_user)
    response = client.post(reverse("students:student_create"), student_form("Vali", "Aliyev", "+998900000002"))
    assert response.status_code == 200 and "tarifida 1 ta faol o'quvchi" in text(response)
    assert Student.objects.count() == 1

    archived = Student.objects.create(first_name="Old", last_name="One", phone="+998900000003", status=Student.Status.ARCHIVED)
    client.post(reverse("students:student_status", args=[archived.pk]), {"status": "active"})
    archived.refresh_from_db()
    assert archived.status == Student.Status.ARCHIVED


def test_import_respects_student_limit(monkeypatch):
    set_center(plan=small_plan(max_students=1))
    rows = [{"first_name": f"S{i}", "last_name": "X", "phone": f"+99890111000{i}"} for i in range(2)]
    monkeypatch.setattr(importer, "parse_students", lambda f: importer.ImportResult(rows=rows))
    result = importer.import_students(None)
    assert result.created == 0 and any("tarifida 1 ta faol o'quvchi" in msg for _, msg in result.errors)
    assert not Student.objects.exists()


def test_user_limit_on_create_and_reactivation(director_user):
    set_center(plan=small_plan(max_users=1))  # director_user — allaqachon 1 ta faol
    client = logged_in(director_user)
    response = client.post(
        reverse("accounts:staff_create"),
        {"username": "newadmin", "role": "administrator", "first_name": "A", "last_name": "B", "password1": PASSWORD, "password2": PASSWORD},
    )
    assert response.status_code == 200 and "tarifida 1 ta faol foydalanuvchi" in text(response)
    assert not User.objects.filter(username="newadmin").exists()
    sleeping = User.objects.create_user("old", password=PASSWORD, role=User.Role.ADMINISTRATOR, is_active=False)
    client.post(reverse("accounts:staff_edit", args=[sleeping.pk]), {"first_name": "", "last_name": "", "role": "administrator", "is_active": "on"})
    sleeping.refresh_from_db()
    assert not sleeping.is_active


def test_center_without_plan_is_unlimited():
    set_center(plan=None)
    for i in range(3):
        Student.objects.create(first_name=f"S{i}", last_name="X", phone=f"+99890000000{i}")
    billing.check_students_limit(1000)
    billing.check_users_limit(1000)


# --- signup, bot and admin -----------------------------------------------------------


def test_signup_starts_trial_on_default_plan():
    c = create_center(name="Ilm", subdomain="ilm", director_username="d", director_password=PASSWORD)
    assert c.plan.code == "standart"
    assert c.trial_ends_at == TODAY + timedelta(days=billing.TRIAL_DAYS - 1)
    assert billing.subscription(c).status == billing.TRIAL


def test_bot_skips_centers_without_full_access():
    c = create_center(name="Ilm", subdomain="ilm", director_username="d", director_password=PASSWORD)
    assert "ilm" in active_center_schemas()
    with schema_context("public"):
        Center.objects.filter(pk=c.pk).update(trial_ends_at=TODAY - timedelta(days=billing.GRACE_DAYS + 2))
    assert "ilm" not in active_center_schemas()


def test_admin_payment_extends_subscription(public_client):
    with schema_context("public"):
        User.objects.create_superuser("yurtora", "", PASSWORD)
    c = set_center(plan=Plan.objects.get(code="start"), trial_ends_at=TODAY - timedelta(days=1))
    public_client.post("/admin/login/", {"username": "yurtora", "password": PASSWORD, "next": "/admin/"})
    response = public_client.post(
        "/admin/tenants/subscriptionpayment/add/",
        {"center": c.pk, "plan": c.plan_id, "months": 3, "amount": 897_000, "method": "manual", "comment": "naqd"},
    )
    assert response.status_code == 302
    payment = SubscriptionPayment.objects.get()
    assert payment.period_start == TODAY and payment.months == 3
    assert Center.objects.get(pk=c.pk).paid_until == payment.period_end
