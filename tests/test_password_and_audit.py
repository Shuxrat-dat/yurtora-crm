"""SEC-13/SEC-14: смена пароля самим пользователем, аудит смены пароля, лимит записей о неудачных входах."""

import pytest
from django.test import Client
from django.urls import reverse

from apps.core import audit
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db
NEW = "Yangi-Xavfsiz-Parol-2026"


def change(client, old, new=NEW, confirm=None):
    return client.post(
        reverse("accounts:password_change"),
        {"old_password": old, "new_password1": new, "new_password2": confirm or new},
    )


def test_user_changes_own_password_and_stays_logged_in(client, teacher_user, password):
    client.force_login(teacher_user)
    response = change(client, password)
    assert response.status_code == 302
    teacher_user.refresh_from_db()
    assert teacher_user.check_password(NEW)
    assert client.get(reverse("accounts:home")).status_code == 200  # сессия сохранена
    assert Client().post(reverse("accounts:login"), {"username": teacher_user.username, "password": password}).status_code == 200  # старый не работает
    assert Client().post(reverse("accounts:login"), {"username": teacher_user.username, "password": NEW}).status_code == 302


@pytest.mark.parametrize("old,new,confirm", [("wrong", NEW, None), (None, "12345678", None), (None, "short1", None), (None, NEW, "Boshqa-Parol-2026")])
def test_wrong_old_weak_or_mismatched_password_is_rejected(client, teacher_user, password, old, new, confirm):
    client.force_login(teacher_user)
    response = change(client, old or password, new, confirm)
    assert response.status_code == 200
    teacher_user.refresh_from_db()
    assert teacher_user.check_password(password)


def test_password_change_requires_login(client):
    assert client.get(reverse("accounts:password_change")).status_code == 302


def test_every_role_can_open_the_page_and_sidebar_links_to_it(client, teacher_user, admin_user, director_user):
    for user in (teacher_user, admin_user, director_user):
        client.force_login(user)
        assert client.get(reverse("accounts:password_change")).status_code == 200
        assert reverse("accounts:password_change") in client.get(reverse("accounts:home")).content.decode()


def test_own_password_change_is_audited_without_secrets(client, teacher_user, password):
    client.force_login(teacher_user)
    change(client, password)
    row = AuditLog.objects.filter(model="accounts.User", object_id=str(teacher_user.pk), action="update").latest("id")
    assert row.username == teacher_user.username
    assert "password" in row.changes
    dump = str(row.changes) + row.object_repr
    assert NEW not in dump and password not in dump and "pbkdf2" not in dump


def test_directors_password_reset_is_audited(client, director_user, teacher_user):
    client.force_login(director_user)
    client.post(reverse("accounts:staff_password", args=[teacher_user.pk]), {"password1": NEW, "password2": NEW})
    row = AuditLog.objects.filter(model="accounts.User", object_id=str(teacher_user.pk)).latest("id")
    assert row.username == director_user.username
    assert "password" in row.changes and NEW not in str(row.changes)


def test_failed_login_flood_is_capped(client, teacher_user):
    limit = audit.LOGIN_FAILED_MAX_PER_WINDOW
    for i in range(limit + 25):
        Client().post(reverse("accounts:login"), {"username": f"x{i}", "password": "bad"})
    assert AuditLog.objects.filter(action="login_failed").count() == limit


def test_login_still_audited_during_a_flood(client, teacher_user, password):
    for i in range(audit.LOGIN_FAILED_MAX_PER_WINDOW + 5):
        Client().post(reverse("accounts:login"), {"username": f"x{i}", "password": "bad"})
    Client().post(reverse("accounts:login"), {"username": teacher_user.username, "password": password})
    assert AuditLog.objects.filter(action="login", username=teacher_user.username).exists()
