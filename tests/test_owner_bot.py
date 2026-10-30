"""Egasi boti: faqat egasi chatlari, barcha markazlar statistikasi, kunlik hisobot bir marta."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone
from django_tenants.utils import get_public_schema_name, schema_context

from apps.courses.models import Course
from apps.finance import services as finance
from apps.groups.models import Enrollment, Group
from apps.notifications import telegram
from apps.students.models import Student
from apps.teachers.models import Teacher
from apps.tenants import owner_bot
from apps.tenants.models import DemoRequest, OwnerDigestLog

pytestmark = pytest.mark.django_db
TZ = ZoneInfo("Asia/Tashkent")
OWNER = 4242


@pytest.fixture
def owner(settings):
    settings.YURTORA_OWNER_BOT_TOKEN = "owner-token"
    settings.YURTORA_OWNER_TELEGRAM_CHAT_ID = f"{OWNER}, 5151"
    settings.YURTORA_OWNER_DIGEST_HOUR = 21


@pytest.fixture
def sent(monkeypatch):
    calls = []
    monkeypatch.setattr(telegram, "send_message", lambda chat_id, text, **kw: calls.append((chat_id, text, kw.get("token"))))
    return calls


@pytest.fixture
def data(director_user):
    today = timezone.localdate()
    teacher = Teacher.objects.create(full_name="Ustoz", phone="+998900000094")
    course = Course.objects.create(name="Matematika", monthly_price=300000)
    group = Group.objects.create(name="Mat-1", course=course, teacher=teacher, start_date=today - timedelta(days=30))
    student = Student.objects.create(first_name="Ali", last_name="Aliyev", phone="", joined_date=today)
    Enrollment.objects.create(student=student, group=group, status="active", start_date=today, paid_from=today)
    finance.register_payment(student, group, 250000, "cash", director_user, today)
    with schema_context(get_public_schema_name()):
        DemoRequest.objects.create(name="Aziz", center_name="Bilim LC", phone="+998901112233")


def msg(chat_id, text, chat_type="private"):
    return {"update_id": 1, "message": {"chat": {"id": chat_id, "type": chat_type}, "text": text}}


def test_stranger_gets_only_own_chat_id(owner, data):
    chat, text = owner_bot.handle_update(msg(999, "/stats"))
    assert chat == 999 and "999" in text and "Test markaz" not in text and "250" not in text
    assert owner_bot.handle_update(msg(OWNER, "/stats", chat_type="group")) is None


def test_stats_cover_all_centers_with_totals(owner, data):
    _, text = owner_bot.handle_update(msg(OWNER, "/stats"))
    assert "Test markaz (testcenter)" in text
    assert "O'quvchilar 1 (+1)" in text and "to'lov 250 000 so'm (1 ta)" in text
    assert "JAMI: " in text and "javob kutayotgan: 1" in text


def test_week_centers_and_demo_commands(owner, data):
    assert "Oxirgi 7 kun" in owner_bot.handle_update(msg(OWNER, "/week"))[1]
    assert "Test markaz" in owner_bot.handle_update(msg(OWNER, "/centers"))[1]
    demo = owner_bot.handle_update(msg(OWNER, "/demo"))[1]
    assert "Bilim LC" in demo and "+998901112233" in demo
    assert "/stats" in owner_bot.handle_update(msg(OWNER, "/start"))[1]


def test_daily_report_once_after_hour_to_all_owner_chats(owner, data, sent):
    day = timezone.localdate()
    assert owner_bot.maybe_send_daily(datetime.combine(day, time(20), TZ)) is False
    assert owner_bot.maybe_send_daily(datetime.combine(day, time(21, 5), TZ)) is True
    assert owner_bot.maybe_send_daily(datetime.combine(day, time(22), TZ)) is False
    assert sorted(chat for chat, _, _ in sent) == [OWNER, 5151]
    assert all(token == "owner-token" and "Kunlik hisobot" in text for _, text, token in sent)
    with schema_context(get_public_schema_name()):
        assert OwnerDigestLog.objects.filter(day=day).count() == 1


def test_not_configured_sends_nothing(settings, data, sent):
    settings.YURTORA_OWNER_BOT_TOKEN = ""
    assert owner_bot.maybe_send_daily(timezone.now()) is False
    assert sent == []


def test_demo_notification_goes_through_owner_bot(owner, sent):
    from apps.tenants import notify

    notify._send("Yangi demo")
    assert sorted((chat, token) for chat, _, token in sent) == [(OWNER, "owner-token"), (5151, "owner-token")]
