"""Direktor xodimlarni yaratadi: login, parol, rol. Boshqa rollar bu bo'limga kira olmaydi."""

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.teachers.models import Teacher

pytestmark = pytest.mark.django_db
GOOD = "Xavfsiz-Parol-2026"


def create(client, **data):
    payload = {
        "username": "new_user", "first_name": "Ali", "last_name": "Valiyev", "role": "administrator",
        "password1": GOOD, "password2": GOOD, "phone": "", "teacher": "",
    }
    payload.update(data)
    return client.post(reverse("accounts:staff_create"), payload)


def test_director_creates_administrator_who_can_log_in(client, director_user):
    client.force_login(director_user)
    resp = create(client)
    assert resp.status_code == 302
    user = User.objects.get(username="new_user")
    assert user.role == "administrator" and user.is_active and not user.is_staff
    assert user.check_password(GOOD) and user.password != GOOD  # xeshlangan

    fresh = Client()
    login = fresh.post(reverse("accounts:login"), {"username": "new_user", "password": GOOD})
    assert login.status_code == 302
    assert fresh.get(reverse("students:student_list")).status_code == 200
    assert fresh.get(reverse("accounts:staff_list")).status_code == 403  # administrator xodimlarni boshqara olmaydi


def test_director_creates_teacher_with_new_card(client, director_user):
    client.force_login(director_user)
    assert create(client, username="ustoz1", role="teacher", phone="+998901112233").status_code == 302
    user = User.objects.get(username="ustoz1")
    card = Teacher.objects.get(user=user)
    assert card.full_name == "Ali Valiyev" and card.phone == "+998901112233"


def test_teacher_links_to_existing_card(client, director_user):
    existing = Teacher.objects.create(full_name="Mavjud Ustoz", phone="+998900000000")
    client.force_login(director_user)
    assert create(client, username="ustoz2", role="teacher", teacher=existing.pk).status_code == 302
    existing.refresh_from_db()
    assert existing.user.username == "ustoz2"
    assert Teacher.objects.count() == 1


def test_teacher_requires_phone_without_card(client, director_user):
    client.force_login(director_user)
    resp = create(client, username="ustoz3", role="teacher")
    assert resp.status_code == 200 and not User.objects.filter(username="ustoz3").exists()


@pytest.mark.parametrize("bad", ["12345678", "short1", "password"])
def test_weak_passwords_rejected(client, director_user, bad):
    client.force_login(director_user)
    resp = create(client, password1=bad, password2=bad)
    assert resp.status_code == 200 and not User.objects.filter(username="new_user").exists()


def test_password_mismatch_and_duplicate_username(client, director_user):
    client.force_login(director_user)
    assert create(client, password2="Boshqa-Parol-2026").status_code == 200
    assert create(client).status_code == 302
    assert create(client).status_code == 200  # login band
    assert User.objects.filter(username="new_user").count() == 1


def test_only_director_can_manage(client, admin_user, teacher_user):
    for user in (admin_user, teacher_user):
        client.force_login(user)
        for name in ("staff_list", "staff_create"):
            assert client.get(reverse(f"accounts:{name}")).status_code == 403
        assert client.post(reverse("accounts:staff_create"), {"username": "x"}).status_code == 403
        assert client.get(reverse("accounts:staff_password", args=[user.pk])).status_code == 403
    assert Client().get(reverse("accounts:staff_list")).status_code == 302


def test_director_resets_password_and_deactivates(client, director_user, teacher_user):
    client.force_login(director_user)
    resp = client.post(
        reverse("accounts:staff_password", args=[teacher_user.pk]), {"password1": GOOD, "password2": GOOD}
    )
    assert resp.status_code == 302
    teacher_user.refresh_from_db()
    assert teacher_user.check_password(GOOD)

    client.post(
        reverse("accounts:staff_edit", args=[teacher_user.pk]),
        {"first_name": "T", "last_name": "U", "role": "teacher"},
    )
    teacher_user.refresh_from_db()
    assert not teacher_user.is_active  # is_active yuborilmadi -> o'chirildi
    blocked = Client().post(reverse("accounts:login"), {"username": teacher_user.username, "password": GOOD})
    assert blocked.status_code == 200  # kira olmaydi


def test_director_cannot_lock_himself_out(client, director_user):
    client.force_login(director_user)
    resp = client.post(
        reverse("accounts:staff_edit", args=[director_user.pk]),
        {"first_name": "D", "last_name": "D", "role": "teacher"},
    )
    assert resp.status_code == 200
    director_user.refresh_from_db()
    assert director_user.role == "director" and director_user.is_active


def test_password_never_written_to_audit_log(client, director_user):
    from apps.core.models import AuditLog

    client.force_login(director_user)
    create(client)
    for log in AuditLog.objects.filter(model="accounts.User"):
        assert GOOD not in str(log.changes) and "pbkdf2" not in str(log.changes)


def test_no_lockout_after_many_failed_logins(client, teacher_user, password):
    """Parol urinishlari cheklanmaydi (django-axes olib tashlangan)."""
    for _ in range(12):
        Client().post(reverse("accounts:login"), {"username": teacher_user.username, "password": "wrong"})
    ok = Client().post(reverse("accounts:login"), {"username": teacher_user.username, "password": password})
    assert ok.status_code == 302
