"""Egasi paneli (tarif berish, uzaytirish, o'chirish, so'rovlarni tasdiqlash) va obuna tugaganda tushuntirish oynasi + to'lov."""

import html
from datetime import timedelta

import pytest
from django.db import connection
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from django_tenants.utils import schema_context

from apps.accounts.models import User
from apps.tenants import billing
from apps.tenants.models import (
    Center,
    PaymentRequest,
    Plan,
    PlatformSettings,
    SubscriptionPayment,
)

pytestmark = pytest.mark.django_db
TODAY = timezone.localdate()
PASSWORD = "Strong-Pass-2026!"


def center():
    # Ommaviy manzilga (panel) so'rovdan keyin connection.tenant public bo'ladi: markaz nomi bo'yicha olinadi
    return Center.objects.get(schema_name="testcenter")


def set_center(**fields):
    Center.objects.filter(schema_name="testcenter").update(**fields)
    return center()


def crm(user=None):
    client = Client(HTTP_HOST="testcenter.localhost")
    if user:
        connection.set_tenant(center())  # sessiya markaz sxemasida saqlansin (panel so'rovidan keyin ulanish public)
        client.force_login(user)
    return client


def text(response):
    return html.unescape(response.content.decode())


@pytest.fixture
def owner():
    """Yurtora xodimi (public sxema) — panelga kiradi."""
    with schema_context("public"):
        User.objects.create_superuser("yurtora", "", PASSWORD)
    client = Client(HTTP_HOST="crm.localhost")
    assert client.post("/admin/login/", {"username": "yurtora", "password": PASSWORD, "next": "/panel/"}).status_code == 302
    connection.set_tenant(center())  # keyingi fixture'lar (direktor) markaz sxemasida yaratilsin
    return client


@pytest.fixture
def notified(monkeypatch):
    sent = []
    monkeypatch.setattr("apps.tenants.notify.notify_owner", lambda text: sent.append(text) or True)
    return sent


@pytest.fixture
def start():
    return Plan.objects.get(code="start")


# --- obuna tugashi: darhol o'chadi va tushuntirish oynasi -------------------------------------


def test_default_expiry_blocks_next_day_with_explanation(director_user, start):
    set_center(plan_id=start.pk, trial_ends_at=TODAY - timedelta(days=1), paid_until=None)
    assert billing.subscription(center()).status == billing.BLOCKED  # imtiyozli kun yo'q (standart 0/0)
    page = crm(director_user).get(reverse("accounts:home"))
    body = text(page)
    assert page.status_code == 403 and "Obuna muddati tugadi" in body
    assert "Ma'lumotlaringiz" in body and "/billing/pay/" in body and start.name in body


def test_last_paid_day_still_works(director_user, start):
    set_center(plan_id=start.pk, trial_ends_at=None, paid_until=TODAY)
    assert crm(director_user).get(reverse("accounts:home")).status_code == 200


def test_payment_details_from_owner_settings_shown(director_user, start):
    PlatformSettings(payment_details="Karta: 8600 1234 5678 9012", support_contact="+998 90 000 00 00").save()
    set_center(plan_id=start.pk, trial_ends_at=TODAY - timedelta(days=3))
    body = text(crm(director_user).get(reverse("accounts:home")))
    assert "8600 1234 5678 9012" in body and "+998 90 000 00 00" in body


def test_expired_center_still_allows_login_and_pay_page(director_user, password, start):
    set_center(plan_id=start.pk, trial_ends_at=TODAY - timedelta(days=10))
    fresh = crm()
    assert fresh.get("/login/").status_code == 200
    assert fresh.post("/login/", {"username": director_user.username, "password": password}).status_code == 302
    pay = fresh.get(reverse("billing:pay"))
    assert pay.status_code == 200 and "To'lov qildim" in text(pay)


def test_director_sends_payment_request_once(director_user, start, notified):
    set_center(plan_id=start.pk, trial_ends_at=TODAY - timedelta(days=2))
    client = crm(director_user)
    data = {"plan": start.pk, "months": 3, "payer_name": "Ali Valiyev", "payer_phone": "+998901112233", "note": "chek 123"}
    assert client.post(reverse("billing:pay"), data).status_code == 302
    req = PaymentRequest.objects.get()
    assert (req.months, req.amount, req.status) == (3, start.price_month * 3, "new")
    assert notified and "To'lov so'rovi" in notified[0] and "Ali Valiyev" in notified[0]
    again = client.post(reverse("billing:pay"), data)
    assert PaymentRequest.objects.count() == 1 and "ko'rib chiqilmoqda" in text(again)


def test_pay_page_is_director_only(admin_user):
    assert crm(admin_user).get(reverse("billing:pay")).status_code == 403


def test_manual_block_closes_everything():
    set_center(is_active=False)
    for path in ("/", "/login/", "/billing/pay/"):
        response = crm().get(path)
        assert response.status_code == 403 and "to'xtatilgan" in text(response)
    assert "/billing/pay/" not in text(crm().get("/"))


def test_staged_mode_still_available(start):
    PlatformSettings(grace_days=2, read_only_days=0).save()
    set_center(plan_id=start.pk, trial_ends_at=TODAY - timedelta(days=1))
    assert billing.subscription(center()).status == billing.GRACE


# --- egasi paneli ---------------------------------------------------------------------------


def test_panel_requires_yurtora_staff():
    anon = Client(HTTP_HOST="crm.localhost").get("/panel/")
    assert anon.status_code == 302 and "/admin/login/" in anon["Location"]
    assert Client(HTTP_HOST="testcenter.localhost").get("/panel/").status_code == 404  # markaz manzilida panel yo'q


def test_panel_lists_centers_with_status(owner, start):
    set_center(plan_id=start.pk, trial_ends_at=TODAY + timedelta(days=3))
    body = text(owner.get("/panel/"))
    assert "Test markaz" in body and "Sinov davri" in body and "7 kunda tugaydi" in body
    assert "Test markaz" in text(owner.get("/panel/?status=expiring"))
    assert "Test markaz" not in text(owner.get("/panel/?status=blocked"))


def test_grant_plan_extends_and_unblocks(owner, director_user, start):
    standart = Plan.objects.get(code="standart")
    set_center(plan_id=start.pk, trial_ends_at=TODAY - timedelta(days=5))
    url = f"/panel/centers/{center().pk}/"
    assert owner.get(url).status_code == 200
    owner.post(url, {"action": "grant", "plan": standart.pk, "months": 2, "amount": "", "method": "manual", "comment": "naqd"})
    payment = SubscriptionPayment.objects.get()
    assert payment.amount == standart.price_month * 2 and payment.plan == standart and payment.period_start == TODAY
    c = center()
    assert c.plan == standart and c.paid_until == payment.period_end
    assert crm(director_user).get(reverse("accounts:home")).status_code == 200


def test_free_bonus_month(owner, start):
    set_center(plan_id=start.pk, trial_ends_at=TODAY + timedelta(days=1))
    owner.post(f"/panel/centers/{center().pk}/", {"action": "grant", "plan": start.pk, "months": 1, "amount": "0", "method": "manual"})
    payment = SubscriptionPayment.objects.get()
    assert payment.amount == 0 and payment.period_start == TODAY + timedelta(days=2)  # sinov kunlari yo'qolmaydi


def test_end_now_and_block_unblock(owner, director_user, start):
    set_center(plan_id=start.pk, paid_until=TODAY + timedelta(days=20), trial_ends_at=None)
    url = f"/panel/centers/{center().pk}/"
    owner.post(url, {"action": "end"})
    assert center().paid_until == TODAY - timedelta(days=1)
    assert "Obuna muddati tugadi" in text(crm(director_user).get(reverse("accounts:home")))
    owner.post(url, {"action": "block"})
    assert center().is_active is False
    owner.post(url, {"action": "unblock"})
    assert center().is_active is True


def test_change_plan_and_trial(owner, start):
    standart = Plan.objects.get(code="standart")
    url = f"/panel/centers/{center().pk}/"
    owner.post(url, {"action": "plan", "plan": standart.pk})
    owner.post(url, {"action": "trial", "trial_ends_at": (TODAY + timedelta(days=30)).isoformat()})
    c = center()
    assert c.plan == standart and c.trial_ends_at == TODAY + timedelta(days=30)


def test_owner_confirms_request_and_center_reopens(owner, director_user, start, notified):
    set_center(plan_id=start.pk, trial_ends_at=TODAY - timedelta(days=4))
    crm(director_user).post(reverse("billing:pay"), {"plan": start.pk, "months": 1, "payer_name": "Ali", "payer_phone": "+998901112233"})
    req = PaymentRequest.objects.get()
    assert "Ali" in text(owner.get("/panel/requests/"))
    owner.post("/panel/requests/", {"request_id": req.pk, "action": "confirm"})
    req.refresh_from_db()
    assert req.status == "confirmed" and req.payment.amount == start.price_month
    assert center().paid_until == req.payment.period_end
    assert crm(director_user).get(reverse("accounts:home")).status_code == 200
    owner.post("/panel/requests/", {"request_id": req.pk, "action": "confirm"})  # ikkinchi marta — uzaytirmaydi
    assert SubscriptionPayment.objects.count() == 1


def test_owner_rejects_request(owner, director_user, start, notified):
    set_center(plan_id=start.pk, trial_ends_at=TODAY - timedelta(days=4))
    crm(director_user).post(reverse("billing:pay"), {"plan": start.pk, "months": 1, "payer_name": "Ali", "payer_phone": "+998901112233"})
    req = PaymentRequest.objects.get()
    owner.post(f"/panel/centers/{center().pk}/", {"action": "reject", "request_id": req.pk, "reason": "pul tushmadi"})
    req.refresh_from_db()
    assert req.status == "rejected" and req.reject_reason == "pul tushmadi"
    assert "pul tushmadi" in text(crm(director_user).get(reverse("billing:pay")))


def test_plans_and_settings_pages(owner):
    owner.post("/panel/plans/", {"name": "Premium", "code": "premium", "price_month": 900000, "sort": 9, "is_active": "on"})
    assert Plan.objects.filter(code="premium", price_month=900000).exists()
    owner.post("/panel/settings/", {"payment_details": "Karta: 9860 ...", "support_contact": "@yurtora", "grace_days": 0, "read_only_days": 0})
    assert PlatformSettings.get().support_contact == "@yurtora"
