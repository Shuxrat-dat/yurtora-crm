"""Harakatlar jurnali (AuditLog) testlari."""

import pytest
from django.urls import reverse

from apps.core.audit import _current
from apps.core.models import AuditLog
from apps.teachers.models import Teacher

pytestmark = pytest.mark.django_db


def test_log_page_director_only(client, director_user, admin_user, teacher_user):
    url = reverse("core:audit_log")
    assert client.get(url).status_code == 302

    client.force_login(teacher_user)
    assert client.get(url).status_code == 403
    client.force_login(admin_user)
    assert client.get(url).status_code == 403
    client.force_login(director_user)
    assert client.get(url).status_code == 200


def test_not_logged_outside_request(db):
    """So'rovsiz o'zgarishlar (management buyruqlari, seed, testlar) jurnalni to'ldirmaydi."""
    Teacher.objects.create(full_name="Ali Valiyev", phone="+998901234567")
    assert AuditLog.objects.filter(model="teachers.Teacher").count() == 0


def test_create_update_delete_logged_with_user(director_user):
    token = _current.set({"user": director_user, "ip": "10.0.0.5"})
    try:
        teacher = Teacher.objects.create(full_name="Ali Valiyev", phone="+998901234567")
        teacher.full_name = "Ali Karimov"
        teacher.save()
        pk = teacher.pk
        teacher.delete()
    finally:
        _current.reset(token)

    created = AuditLog.objects.get(model="teachers.Teacher", action="create")
    assert created.username == director_user.username
    assert created.object_id == str(pk)
    assert created.ip_address == "10.0.0.5"

    upd = AuditLog.objects.get(model="teachers.Teacher", action="update")
    assert upd.changes["full_name"] == ["Ali Valiyev", "Ali Karimov"]

    assert AuditLog.objects.filter(model="teachers.Teacher", action="delete").exists()


def test_password_never_logged(director_user, teacher_user):
    teacher_user.first_name = "Yangi"
    teacher_user.set_password("AnotherSecret123!")
    token = _current.set({"user": director_user, "ip": "1.1.1.1"})
    try:
        teacher_user.save()
    finally:
        _current.reset(token)
    log = AuditLog.objects.get(model="accounts.User", action="update")
    assert "password" not in log.changes
    assert "first_name" in log.changes


def test_login_and_failed_login_logged(client, teacher_user, password):
    client.post(reverse("accounts:login"), {"username": teacher_user.username, "password": "bad"})
    assert AuditLog.objects.filter(action="login_failed", username=teacher_user.username).exists()
    client.post(reverse("accounts:login"), {"username": teacher_user.username, "password": password})
    assert AuditLog.objects.filter(action="login", user=teacher_user).exists()


def test_log_entries_are_immutable(director_user):
    entry = AuditLog.objects.create(user=director_user, username="x", action="login")
    with pytest.raises(ValueError):
        entry.save()
    with pytest.raises(ValueError):
        entry.delete()


def test_http_request_is_attributed_to_user(client, director_user):
    """Haqiqiy HTTP so'rovi (middleware orqali) o'zgarishni foydalanuvchiga bog'laydi."""
    client.force_login(director_user)
    resp = client.post(
        reverse("teachers:teacher_create"),
        {"full_name": "Sardor Rustamov", "phone": "+998909998877", "pay_type": "percent", "is_active": "on"},
        REMOTE_ADDR="10.1.2.3",
    )
    teacher = Teacher.objects.filter(full_name="Sardor Rustamov").first()
    assert teacher is not None, resp.content[:500]
    log = AuditLog.objects.get(model="teachers.Teacher", action="create")
    assert log.user == director_user
    assert log.ip_address == "10.1.2.3"
