"""Haftalik qarz ro'yxati: xodim chati, tasdiqlash, jadval. Tarmoq chaqiruvlari soxta (mock)."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.urls import reverse

from apps.courses.models import Course
from apps.finance.services import add_adjustment
from apps.groups.models import Group
from apps.notifications import digest, routing, services, telegram
from apps.notifications.models import (
    AdminChat,
    DebtDigest,
    NotificationLog,
    TelegramContact,
)
from apps.students.models import Student
from apps.teachers.models import Teacher

pytestmark = pytest.mark.django_db
TZ = ZoneInfo("Asia/Tashkent")


@pytest.fixture
def api(monkeypatch):
    """Haqiqiy Telegram o'rniga chaqiruvlarni yig'adi."""
    calls = {"messages": [], "answers": [], "removed": []}

    def send(chat_id, text, reply_markup=None):
        calls["messages"].append((chat_id, text, reply_markup))
        return {"message_id": 1}

    monkeypatch.setattr(telegram, "send_message", send)
    monkeypatch.setattr(
        telegram, "answer_callback_query", lambda cid, text="": calls["answers"].append(text)
    )
    monkeypatch.setattr(telegram, "remove_buttons", lambda chat, mid: calls["removed"].append(mid))
    return calls


@pytest.fixture
def configured(settings):
    settings.TELEGRAM_BOT_TOKEN = "123:TEST"
    settings.TELEGRAM_BOT_USERNAME = "yurtora_test_bot"
    settings.TELEGRAM_REMINDER_COOLDOWN_DAYS = 3
    settings.TELEGRAM_DIGEST_WEEKDAY = 0
    settings.TELEGRAM_DIGEST_HOUR = 9


@pytest.fixture
def school(director_user):
    course = Course.objects.create(name="English", monthly_price=500000)
    teacher = Teacher.objects.create(full_name="Ustoz", phone="+998900000000")
    group = Group.objects.create(name="G1", course=course, teacher=teacher, start_date="2026-01-01")

    def make_debtor(first, amount, telegram_chat=None):
        student = Student.objects.create(first_name=first, last_name="Test", phone="+998901112233")
        add_adjustment(student, group, -amount, director_user, "qarz")
        if telegram_chat:
            TelegramContact.objects.create(student=student, chat_id=telegram_chat)
        return student

    return make_debtor


@pytest.fixture
def admin_chat(admin_user):
    return AdminChat.objects.create(user=admin_user, chat_id=900)


def callback(chat_id, data, chat_type="private"):
    return {
        "id": "cb1",
        "data": data,
        "message": {"message_id": 5, "chat": {"id": chat_id, "type": chat_type}},
    }


# --- xodim chatini ulash -------------------------------------------------------


def test_admin_link_flow(admin_user, configured):
    token = digest.create_admin_token(admin_user)
    payload = f"{connection.schema_name}_admin_{token.token}"  # bitta bot: havolada markaz kodi
    assert digest.admin_deep_link(token).endswith(f"?start={payload}")

    update = {"message": {"chat": {"id": 900, "type": "private"}, "text": f"/start {payload}"}}
    chat_id, reply = routing.handle_update(update)
    assert chat_id == 900 and admin_user.username in reply
    assert AdminChat.objects.get(chat_id=900).user == admin_user
    # bir marta ishlaydi
    assert digest.link_admin_chat(token.token, 901) is None

    routing.handle_update({"message": {"chat": {"id": 900, "type": "private"}, "text": "/stop"}})
    assert not AdminChat.objects.get(chat_id=900).is_active


def test_admin_chat_page_access(client, admin_user, teacher_user, configured):
    url = reverse("notifications:admin_chat")
    client.force_login(teacher_user)
    assert client.get(url).status_code == 403
    client.force_login(admin_user)
    assert client.get(url).status_code == 200
    resp = client.post(url)
    assert f"start={connection.schema_name}_admin_" in resp.context["link"]


# --- ro'yxat ---------------------------------------------------------------


def test_digest_includes_only_sendable(school, admin_chat, configured):
    school("Linked", 300000, telegram_chat=1)
    school("NoTelegram", 900000)
    recent = school("Recent", 200000, telegram_chat=2)
    NotificationLog.objects.create(student=recent, chat_id=2, kind="debt", text="x", status="sent")

    created, rows = digest.create_digest()
    assert [r["student"].first_name for r in rows] == ["Linked"]
    assert created.status == DebtDigest.Status.PENDING
    assert created.total_debt == 300000


def test_empty_digest_is_marked_so_week_is_not_recomputed(admin_chat, configured):
    created, rows = digest.create_digest()
    assert rows == [] and created.status == DebtDigest.Status.CANCELLED


# --- tasdiqlash ------------------------------------------------------------


def test_confirm_sends_once_and_double_press_is_ignored(school, admin_chat, configured, api):
    school("Linked", 300000, telegram_chat=1)
    created, rows = digest.create_digest()
    digest.notify_admins(created, rows)
    assert api["messages"][0][0] == 900 and api["messages"][0][2]["inline_keyboard"]
    api["messages"].clear()

    digest.handle_callback(callback(900, f"send:{created.pk}"))
    services.process_pending_notifications(pause=0)  # bot navbatni yuboradi
    sent_to = [chat for chat, _, _ in api["messages"]]
    assert 1 in sent_to  # ota-onaga ketdi
    assert NotificationLog.objects.filter(status="sent", chat_id=1).count() == 1
    created.refresh_from_db()
    assert created.status == DebtDigest.Status.SENT

    api["messages"].clear()
    digest.handle_callback(callback(900, f"send:{created.pk}"))
    assert [chat for chat, _, _ in api["messages"]] == [900]  # faqat "allaqachon qayta ishlangan"
    assert NotificationLog.objects.filter(status="sent").count() == 1


def test_cancel_sends_nothing(school, admin_chat, configured, api):
    school("Linked", 300000, telegram_chat=1)
    created, rows = digest.create_digest()
    digest.handle_callback(callback(900, f"cancel:{created.pk}"))
    assert NotificationLog.objects.count() == 0
    created.refresh_from_db()
    assert created.status == DebtDigest.Status.CANCELLED


def test_callback_from_unknown_or_group_chat_is_rejected(school, admin_chat, configured, api):
    school("Linked", 300000, telegram_chat=1)
    created, _ = digest.create_digest()
    digest.handle_callback(callback(555, f"send:{created.pk}"))  # ulanmagan chat
    digest.handle_callback(callback(900, f"send:{created.pk}", chat_type="group"))
    assert NotificationLog.objects.count() == 0
    created.refresh_from_db()
    assert created.status == DebtDigest.Status.PENDING
    assert "Ruxsat yo'q" in api["answers"]


def test_expired_digest_is_not_sent(school, admin_chat, configured, api):
    school("Linked", 300000, telegram_chat=1)
    created, _ = digest.create_digest()
    DebtDigest.objects.filter(pk=created.pk).update(
        created_at=created.created_at - timedelta(hours=25)
    )
    digest.handle_callback(callback(900, f"send:{created.pk}"))
    assert NotificationLog.objects.count() == 0
    created.refresh_from_db()
    assert created.status == DebtDigest.Status.EXPIRED


def test_student_who_paid_after_digest_is_skipped(
    school, admin_chat, configured, api, director_user
):
    student = school("Linked", 300000, telegram_chat=1)
    created, _ = digest.create_digest()
    group = Group.objects.get()
    add_adjustment(student, group, 300000, director_user, "to'ladi")  # qarz yopildi
    digest.handle_callback(callback(900, f"send:{created.pk}"))
    assert NotificationLog.objects.count() == 0


def test_tampered_callback_data_is_harmless(admin_chat, configured, api):
    digest.handle_callback(callback(900, "send:abc"))
    digest.handle_callback(callback(900, "drop:1"))
    digest.handle_callback(callback(900, "send:99999"))
    assert NotificationLog.objects.count() == 0


# --- jadval ----------------------------------------------------------------


def test_weekly_schedule(school, admin_chat, configured, api):
    school("Linked", 300000, telegram_chat=1)
    monday_8 = datetime(2026, 10, 12, 8, 0, tzinfo=TZ)
    monday_10 = datetime(2026, 10, 12, 10, 0, tzinfo=TZ)

    assert digest.maybe_send_weekly_digest(monday_8) is None  # hali erta
    sent = digest.maybe_send_weekly_digest(monday_10)
    assert sent is not None and sent.status == DebtDigest.Status.PENDING
    assert [chat for chat, _, _ in api["messages"]] == [900]

    api["messages"].clear()
    # shu hafta ro'yxat allaqachon bor
    assert digest.maybe_send_weekly_digest(monday_10 + timedelta(minutes=1)) is None
    assert api["messages"] == []


def test_weekly_not_sent_without_admin_chat_or_token(school, configured, api, settings):
    school("Linked", 300000, telegram_chat=1)
    monday_10 = datetime(2026, 10, 12, 10, 0, tzinfo=TZ)
    assert digest.maybe_send_weekly_digest(monday_10) is None  # xodim chati yo'q
    AdminChat.objects.create(chat_id=900)
    settings.TELEGRAM_BOT_TOKEN = ""
    assert digest.maybe_send_weekly_digest(monday_10) is None  # bot sozlanmagan
