"""SEC-12: чат сотрудника в Telegram перестаёт работать, когда сотрудника отключили или перевели в учителя."""

import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.notifications import digest, telegram
from apps.notifications.models import AdminChat, DebtDigest

pytestmark = pytest.mark.django_db


@pytest.fixture
def api(monkeypatch):
    calls = {"messages": [], "answers": []}
    monkeypatch.setattr(telegram, "send_message", lambda chat_id, text, reply_markup=None: calls["messages"].append((chat_id, text)))
    monkeypatch.setattr(telegram, "answer_callback_query", lambda cid, text="": calls["answers"].append(text))
    monkeypatch.setattr(telegram, "remove_buttons", lambda chat, mid: None)
    return calls


@pytest.fixture
def staff_chat(admin_user):
    return AdminChat.objects.create(user=admin_user, chat_id=555, is_active=True)


def edit(client, director, target, **fields):
    client.force_login(director)
    data = {"first_name": "A", "last_name": "B", "role": target.role, "is_active": "on"}
    data.update(fields)
    return client.post(reverse("accounts:staff_edit", args=[target.pk]), data)


def callback(chat_id, digest_id):
    return {"id": "1", "message": {"chat": {"id": chat_id, "type": "private"}, "message_id": 9}, "data": f"cancel:{digest_id}"}


def test_deactivating_user_switches_off_their_chat(client, director_user, admin_user, staff_chat):
    response = edit(client, director_user, admin_user, is_active="")
    assert response.status_code == 302
    staff_chat.refresh_from_db()
    assert staff_chat.is_active is False


def test_demoting_to_teacher_switches_off_their_chat(client, director_user, admin_user, staff_chat):
    edit(client, director_user, admin_user, role="teacher")
    staff_chat.refresh_from_db()
    assert staff_chat.is_active is False


def test_editing_name_keeps_the_chat_active(client, director_user, admin_user, staff_chat):
    edit(client, director_user, admin_user, first_name="Yangi")
    staff_chat.refresh_from_db()
    assert staff_chat.is_active is True


def test_callbacks_from_a_deactivated_user_are_refused_even_if_chat_flag_is_stale(admin_user, staff_chat, api):
    """Если флаг чата остался включённым (старые данные), пользователь с отключённой учёткой всё равно отклоняется."""
    User.objects.filter(pk=admin_user.pk).update(is_active=False)
    pending = DebtDigest.objects.create(student_ids=[], total_debt=0, status=DebtDigest.Status.PENDING)
    digest.handle_callback(callback(555, pending.pk))
    pending.refresh_from_db()
    assert pending.status == DebtDigest.Status.PENDING  # не обработано
    assert api["answers"] == ["Ruxsat yo'q"]


def test_active_staff_callback_still_works(staff_chat, api):
    pending = DebtDigest.objects.create(student_ids=[], total_debt=0, status=DebtDigest.Status.PENDING)
    digest.handle_callback(callback(555, pending.pk))
    pending.refresh_from_db()
    assert pending.status == DebtDigest.Status.CANCELLED


def test_weekly_list_goes_only_to_current_staff(admin_user, teacher_user, api):
    AdminChat.objects.create(user=admin_user, chat_id=1, is_active=True)
    AdminChat.objects.create(user=teacher_user, chat_id=2, is_active=True)  # учитель в этом списке быть не должен
    stale = User.objects.create_user("gone", password="x", role="administrator", is_active=False)
    AdminChat.objects.create(user=stale, chat_id=3, is_active=True)
    digest_obj = DebtDigest.objects.create(student_ids=[], total_debt=0)
    assert digest.notify_admins(digest_obj, []) == 1
    assert [chat for chat, _ in api["messages"]] == [1]
