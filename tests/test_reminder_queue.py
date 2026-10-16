"""BOT-02/BOT-03: рассылка идёт через очередь; веб-запрос быстрый, дубликатов и потерь нет."""

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse

from apps.courses.models import Course
from apps.finance.services import add_adjustment
from apps.groups.models import Group
from apps.notifications import digest, services, telegram
from apps.notifications.models import (
    AdminChat,
    DebtDigest,
    NotificationLog,
    TelegramContact,
)
from apps.students.models import Student
from apps.teachers.models import Teacher

pytestmark = pytest.mark.django_db


@pytest.fixture
def sent(monkeypatch):
    calls = []
    monkeypatch.setattr(telegram, "send_message", lambda chat_id, text, reply_markup=None: calls.append((chat_id, text)))
    monkeypatch.setattr(telegram, "answer_callback_query", lambda cid, text="": None)
    monkeypatch.setattr(telegram, "remove_buttons", lambda chat, mid: None)
    return calls


@pytest.fixture
def configured(settings):
    settings.TELEGRAM_BOT_TOKEN = "123:TEST"
    settings.TELEGRAM_BOT_USERNAME = "yurtora_test_bot"
    settings.TELEGRAM_REMINDER_COOLDOWN_DAYS = 3


@pytest.fixture
def school(director_user):
    teacher = Teacher.objects.create(full_name="Ustoz", phone="+998900000000")
    group = Group.objects.create(name="G1", course=Course.objects.create(name="English", monthly_price=500000), teacher=teacher, start_date="2026-01-01")

    def debtor(name, chat_ids=(), amount=300000):
        student = Student.objects.create(first_name=name, last_name="Test", phone="+998901112233")
        add_adjustment(student, group, -amount, director_user, "qarz")
        for chat_id in chat_ids:
            TelegramContact.objects.create(student=student, chat_id=chat_id)
        return student

    return debtor


def groups_of(student):
    return next(d for d in services.debts_by_student() if d["student"].pk == student.pk)["groups"]


def test_queue_creates_pending_logs_and_sends_nothing(school, sent, admin_user):
    student = school("Ali", chat_ids=(11, 12))
    assert services.queue_debt_reminder(student, groups_of(student), admin_user) == 2
    assert sent == []
    logs = NotificationLog.objects.filter(status="pending")
    assert logs.count() == 2 and all(log.sent_by == admin_user and "300 000" in log.text for log in logs)


def test_double_queue_does_not_duplicate(school, sent):
    student = school("Ali", chat_ids=(11,))
    assert services.queue_debt_reminder(student, groups_of(student)) == 1
    assert services.queue_debt_reminder(student, groups_of(student)) == 0
    assert NotificationLog.objects.count() == 1


def test_database_blocks_duplicate_pending_rows(school):
    student = school("Ali", chat_ids=(11,))
    base = {"student": student, "chat_id": 11, "kind": "debt", "text": "x", "status": "pending"}
    NotificationLog.objects.create(**base)
    with pytest.raises(IntegrityError), transaction.atomic():
        NotificationLog.objects.create(**base)
    NotificationLog.objects.create(**{**base, "status": "sent"})  # отправленные не ограничиваются


def test_bot_sends_pending_and_marks_them(school, sent):
    student = school("Ali", chat_ids=(11, 12))
    services.queue_debt_reminder(student, groups_of(student))
    assert services.pending_count() == 2
    assert services.process_pending_notifications(pause=0) == 2
    assert sorted(chat for chat, _ in sent) == [11, 12]
    assert services.pending_count() == 0 and NotificationLog.objects.filter(status="sent").count() == 2
    assert services.process_pending_notifications(pause=0) == 0  # повторно не отправляется


def test_batch_limit_leaves_the_rest_queued(school, sent):
    for i in range(5):
        student = school(f"S{i}", chat_ids=(100 + i,))
        services.queue_debt_reminder(student, groups_of(student))
    assert services.process_pending_notifications(limit=2, pause=0) == 2
    assert services.pending_count() == 3
    assert services.process_pending_notifications(limit=10, pause=0) == 3


def test_blocked_chat_marks_failed_and_deactivates_only_that_contact(school, monkeypatch):
    student = school("Ali", chat_ids=(11, 12))
    services.queue_debt_reminder(student, groups_of(student))

    def send(chat_id, text, reply_markup=None):
        if chat_id == 11:
            raise telegram.TelegramError("Telegram HTTP 403", blocked=True)

    monkeypatch.setattr(telegram, "send_message", send)
    services.process_pending_notifications(pause=0)
    assert NotificationLog.objects.get(chat_id=11).status == "failed"
    assert NotificationLog.objects.get(chat_id=12).status == "sent"
    assert not TelegramContact.objects.get(chat_id=11).is_active and TelegramContact.objects.get(chat_id=12).is_active


def test_temporary_error_does_not_lose_other_messages(school, monkeypatch):
    first, second = school("A", chat_ids=(21,)), school("B", chat_ids=(22,))
    for s in (first, second):
        services.queue_debt_reminder(s, groups_of(s))
    seen = []

    def send(chat_id, text, reply_markup=None):
        seen.append(chat_id)
        if chat_id == 21:
            raise telegram.TelegramError("timeout")

    monkeypatch.setattr(telegram, "send_message", send)
    services.process_pending_notifications(pause=0)
    assert seen == [21, 22]
    assert NotificationLog.objects.get(chat_id=21).status == "failed" and NotificationLog.objects.get(chat_id=22).status == "sent"


def test_pending_counts_towards_cooldown(school, sent):
    student = school("Ali", chat_ids=(11,))
    services.queue_debt_reminder(student, groups_of(student))
    assert student.pk in services.last_reminder_at([student.pk])
    assert digest.sendable_rows() == []  # уже в очереди: в сводку не попадает


def test_bulk_view_is_fast_and_queues(client, admin_user, school, sent, configured):
    students = [school(f"S{i}", chat_ids=(200 + i,)) for i in range(30)]
    client.force_login(admin_user)
    response = client.post(reverse("notifications:debt_reminders"), {"student": [str(s.pk) for s in students]}, follow=True)
    assert response.status_code == 200 and sent == []  # ни одного обращения к Telegram в запросе
    assert NotificationLog.objects.filter(status="pending").count() == 30
    assert any("Navbatga qo'yildi: 30" in str(m) for m in response.context["messages"])


def test_confirmed_digest_is_queued_atomically_with_its_status(school, sent, configured, monkeypatch):
    """BOT-03: статус «отправлен» и очередь меняются вместе, поэтому сбой не теряет получателей."""
    admin = AdminChat.objects.create(user=None, chat_id=900, is_active=True)
    for i in range(3):
        school(f"S{i}", chat_ids=(300 + i,))
    created, _rows = digest.create_digest()
    calls = {"n": 0}
    real = services.queue_debt_reminder

    def flaky(student, groups, user=None):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("crash while queueing")
        return real(student, groups, user)

    monkeypatch.setattr(services, "queue_debt_reminder", flaky)
    with pytest.raises(RuntimeError):
        digest.resolve_digest(created.pk, admin.chat_id, "send")
    created.refresh_from_db()
    assert created.status == DebtDigest.Status.PENDING  # откатилось целиком
    assert NotificationLog.objects.count() == 0

    monkeypatch.setattr(services, "queue_debt_reminder", real)
    message = digest.resolve_digest(created.pk, admin.chat_id, "send")  # повтор после сбоя работает
    assert "Navbatga qo'yildi: 3" in message
    assert NotificationLog.objects.filter(status="pending").count() == 3
    created.refresh_from_db()
    assert created.status == DebtDigest.Status.SENT
