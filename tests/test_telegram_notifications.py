"""Telegram bot: ulash, qarz eslatmasi, bot buyruqlari. Tarmoq chaqiruvlari soxta (mock)."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.courses.models import Course
from apps.finance.services import add_adjustment
from apps.groups.models import Group
from apps.notifications import services, telegram
from apps.notifications.models import (
    NotificationLog,
    TelegramContact,
    TelegramLinkToken,
)
from apps.students.models import Student
from apps.teachers.models import Teacher

pytestmark = pytest.mark.django_db


@pytest.fixture
def sent(monkeypatch):
    """Haqiqiy Telegram o'rniga yuborilganlarni yig'adi."""
    messages = []

    def fake_send(chat_id, text):
        messages.append((chat_id, text))
        return {"message_id": 1}

    monkeypatch.setattr(telegram, "send_message", fake_send)
    return messages


@pytest.fixture
def configured(settings):
    settings.TELEGRAM_BOT_TOKEN = "123:TEST"
    settings.TELEGRAM_BOT_USERNAME = "yurtora_test_bot"
    settings.TELEGRAM_REMINDER_COOLDOWN_DAYS = 3


@pytest.fixture
def debtor(director_user):
    course = Course.objects.create(name="English", monthly_price=500000)
    teacher = Teacher.objects.create(full_name="Ustoz", phone="+998900000000")
    group = Group.objects.create(
        name="G1", course=course, teacher=teacher, start_date="2026-01-01"
    )
    student = Student.objects.create(first_name="Ali", last_name="Valiyev", phone="+998901112233")
    add_adjustment(student, group, -300000, director_user, "test qarz")
    return student


# --- ulash ---------------------------------------------------------------


def test_link_token_flow_and_single_use(debtor, configured, admin_user):
    token = services.create_link_token(debtor, admin_user)
    assert services.deep_link(token) == f"https://t.me/yurtora_test_bot?start={token.token}"

    assert services.link_chat(token.token, 555, "parent") == debtor
    assert TelegramContact.objects.filter(student=debtor, chat_id=555, is_active=True).exists()
    # ikkinchi marta ishlamaydi
    assert services.link_chat(token.token, 777) is None


def test_expired_or_unknown_token_rejected(debtor):
    expired = TelegramLinkToken.objects.create(
        student=debtor, token="old", expires_at=timezone.now() - timedelta(hours=1)
    )
    assert services.link_chat(expired.token, 1) is None
    assert services.link_chat("nope", 1) is None
    assert not TelegramContact.objects.exists()


def test_bot_start_and_stop_commands(debtor, admin_user):
    token = services.create_link_token(debtor, admin_user)
    update = {
        "message": {
            "chat": {"id": 42, "type": "private"},
            "text": f"/start {token.token}",
            "from": {"username": "mom"},
        }
    }
    chat_id, reply = services.handle_update(update)
    assert chat_id == 42 and "Valiyev" in reply

    stop = {"message": {"chat": {"id": 42, "type": "private"}, "text": "/stop"}}
    services.handle_update(stop)
    assert not TelegramContact.objects.get(chat_id=42).is_active


def test_bot_ignores_groups_and_plain_text(debtor, admin_user):
    token = services.create_link_token(debtor, admin_user)
    group_update = {
        "message": {"chat": {"id": -9, "type": "group"}, "text": f"/start {token.token}"}
    }
    assert services.handle_update(group_update) is None
    plain = {"message": {"chat": {"id": 1, "type": "private"}, "text": "salom"}}
    assert services.handle_update(plain) is None
    assert not TelegramContact.objects.exists()


# --- eslatma ---------------------------------------------------------------


def test_message_contains_amount_and_group(debtor):
    entry = services.debts_by_student()[0]
    text = services.build_debt_message(entry["student"], entry["groups"])
    assert "300 000" in text and "G1" in text and "Valiyev" in text


def test_student_page_sends_reminder(client, admin_user, debtor, configured, sent):
    TelegramContact.objects.create(student=debtor, chat_id=555)
    client.force_login(admin_user)
    resp = client.post(
        reverse("notifications:student_telegram", args=[debtor.pk]), {"action": "send_reminder"}
    )
    assert resp.status_code == 200
    assert [chat for chat, _ in sent] == [555]
    log = NotificationLog.objects.get()
    assert log.status == "sent" and log.sent_by == admin_user


def test_bulk_send_respects_selection_telegram_and_cooldown(
    client, admin_user, debtor, configured, sent
):
    TelegramContact.objects.create(student=debtor, chat_id=555)
    client.force_login(admin_user)
    url = reverse("notifications:debt_reminders")

    assert client.get(url).context["sendable"] == 1

    client.post(url, {"student": [str(debtor.pk)]})
    assert sent == []  # veb-so'rov yubormaydi: navbatga qo'yadi
    services.process_pending_notifications(pause=0)  # bot navbatni yuboradi
    assert len(sent) == 1

    # 3 kun ichida qayta yuborilmaydi
    client.post(url, {"student": [str(debtor.pk)]})
    services.process_pending_notifications(pause=0)
    assert len(sent) == 1
    assert client.get(url).context["sendable"] == 0

    # tanlanmagan o'quvchiga yuborilmaydi
    NotificationLog.objects.all().delete()
    client.post(url, {})
    services.process_pending_notifications(pause=0)
    assert len(sent) == 1


def test_blocked_chat_is_deactivated_and_logged(
    client, admin_user, debtor, configured, monkeypatch
):
    TelegramContact.objects.create(student=debtor, chat_id=555)

    def boom(chat_id, text):
        raise telegram.TelegramError("Telegram HTTP 403", blocked=True)

    monkeypatch.setattr(telegram, "send_message", boom)
    client.force_login(admin_user)
    client.post(
        reverse("notifications:student_telegram", args=[debtor.pk]), {"action": "send_reminder"}
    )
    assert not TelegramContact.objects.get(chat_id=555).is_active
    assert NotificationLog.objects.get().status == "failed"


def test_not_configured_blocks_sending(client, admin_user, debtor, settings, sent):
    settings.TELEGRAM_BOT_TOKEN = ""
    TelegramContact.objects.create(student=debtor, chat_id=555)
    client.force_login(admin_user)
    client.post(reverse("notifications:debt_reminders"), {"student": [str(debtor.pk)]})
    assert sent == []


def test_teacher_cannot_access(client, teacher_user, debtor):
    client.force_login(teacher_user)
    assert client.get(reverse("notifications:debt_reminders")).status_code == 403
    student_url = reverse("notifications:student_telegram", args=[debtor.pk])
    assert client.get(student_url).status_code == 403


# --- HTTP mijoz --------------------------------------------------------------


def test_http_403_maps_to_blocked_and_hides_token(configured, monkeypatch):
    import urllib.error

    def raise_403(*args, **kwargs):
        raise urllib.error.HTTPError(
            "https://api.telegram.org/bot123:TEST/sendMessage", 403, "Forbidden", {}, None
        )

    monkeypatch.setattr("urllib.request.urlopen", raise_403)
    with pytest.raises(telegram.TelegramError) as info:
        telegram.send_message(1, "x")
    assert info.value.blocked is True
    assert "123:TEST" not in str(info.value)


def test_send_without_token_raises(settings):
    settings.TELEGRAM_BOT_TOKEN = ""
    with pytest.raises(telegram.TelegramError):
        telegram.send_message(1, "x")
