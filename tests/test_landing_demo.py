"""Reklama lendingi va "Demo buyurtma": zayavka public sxemada saqlanadi, egasiga xabar boradi, spamdan himoya."""

import pytest
from django_tenants.utils import get_public_schema_name, schema_context

from apps.tenants import notify
from apps.tenants.models import DemoRequest

pytestmark = pytest.mark.django_db

VALID = {"name": "Aziz", "center_name": "Ilm Academy", "phone": "+998 90 123 45 67", "students": "100-300", "message": "Ertaga qo'ng'iroq qiling", "plan": "standart", "website": ""}


def demos():
    with schema_context(get_public_schema_name()):
        return list(DemoRequest.objects.order_by("id"))


def test_landing_is_an_ad_page_with_plans_and_form(public_client):
    html = public_client.get("/").content.decode()
    for needle in ("7 kunlik demo buyurtma", 'id="demo"', "Qarzdorlarga Telegram eslatma", "299 000", 'name="phone"', "Markazni ulash"):
        assert needle in html, needle


def test_plan_from_pricing_card_prefills_hidden_field(public_client):
    html = public_client.get("/?plan=standart").content.decode()
    assert 'name="plan"' in html and 'value="standart"' in html


def test_valid_request_is_saved_and_redirects_to_thanks(public_client):
    response = public_client.post("/", VALID, REMOTE_ADDR="10.0.0.1")
    assert response.status_code == 302 and response.url == "/demo/rahmat/"
    [demo] = demos()
    assert (demo.center_name, demo.name, demo.students, demo.plan, demo.status, demo.ip_address) == ("Ilm Academy", "Aziz", "100-300", "standart", "new", "10.0.0.1")
    thanks = public_client.get("/demo/rahmat/")
    assert thanks.status_code == 200 and "Rahmat" in thanks.content.decode()


@pytest.mark.parametrize("phone", ["123", "abc", ""])
def test_bad_phone_rejected(public_client, phone):
    response = public_client.post("/", {**VALID, "phone": phone})
    assert response.status_code == 200 and demos() == []


def test_honeypot_bots_get_success_but_nothing_is_stored(public_client):
    response = public_client.post("/", {**VALID, "website": "http://spam.example"})
    assert response.status_code == 302 and demos() == []


def test_same_phone_twice_in_a_day_makes_one_request(public_client):
    public_client.post("/", VALID)
    again = public_client.post("/", {**VALID, "phone": "90-123-45-67", "name": "Boshqa"})
    assert again.status_code == 302 and len(demos()) == 1


def test_ip_limit_per_hour(public_client):
    for i in range(3):
        assert public_client.post("/", {**VALID, "phone": f"+99890123450{i}"}, REMOTE_ADDR="10.0.0.9").status_code == 302
    blocked = public_client.post("/", {**VALID, "phone": "+998901234599"}, REMOTE_ADDR="10.0.0.9")
    assert blocked.status_code == 200 and "Juda ko&#x27;p buyurtma" in blocked.content.decode()
    assert len(demos()) == 3
    other_ip = public_client.post("/", {**VALID, "phone": "+998901234588"}, REMOTE_ADDR="10.0.0.10")
    assert other_ip.status_code == 302 and len(demos()) == 4


def test_owner_is_notified_when_telegram_is_configured(public_client, settings, monkeypatch):
    sent = []
    settings.TELEGRAM_BOT_TOKEN = "token"
    settings.YURTORA_OWNER_TELEGRAM_CHAT_ID = "12345"

    class Inline:
        def __init__(self, target, args, daemon):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(notify.threading, "Thread", Inline)
    monkeypatch.setattr("apps.notifications.telegram.send_message", lambda chat, text: sent.append((chat, text)))
    public_client.post("/", VALID)
    [(chat, text)] = sent
    assert chat == "12345" and "Ilm Academy" in text and "+998 90 123 45 67" in text and "Tarif: standart" in text


def test_no_notification_when_not_configured_and_failures_never_break_the_form(public_client, settings, monkeypatch):
    settings.TELEGRAM_BOT_TOKEN = ""
    settings.YURTORA_OWNER_TELEGRAM_CHAT_ID = ""
    assert notify.notify_owner("x") is False
    assert public_client.post("/", VALID).status_code == 302

    settings.TELEGRAM_BOT_TOKEN, settings.YURTORA_OWNER_TELEGRAM_CHAT_ID = "token", "1"

    def boom(chat, text):
        raise RuntimeError("tarmoq")

    monkeypatch.setattr("apps.notifications.telegram.send_message", boom)
    notify._send("matn")  # istisno tashqariga chiqmaydi


def test_owner_sees_requests_in_admin(public_client):
    from django.contrib.auth import get_user_model

    with schema_context(get_public_schema_name()):
        get_user_model().objects.create_superuser("owner", "", "x" * 12)
        DemoRequest.objects.create(name="A", center_name="Ilm Academy", phone="+998901234567")
    public_client.post("/admin/login/", {"username": "owner", "password": "x" * 12, "next": "/admin/"})
    html = public_client.get("/admin/tenants/demorequest/").content.decode()
    assert "Ilm Academy" in html and "+998901234567" in html
