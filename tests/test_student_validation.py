"""STU-01: телефон приводится к +998XXXXXXXXX, дубликаты учеников не создаются случайно."""

import pytest
from django.urls import reverse

from apps.students.forms import GuardianForm, StudentForm
from apps.students.models import Student

pytestmark = pytest.mark.django_db


def data(**extra):
    base = {"first_name": "Ali", "last_name": "Valiyev", "phone": "+998901112233", "status": "active", "joined_date": "2026-10-01"}
    base.update(extra)
    return base


@pytest.mark.parametrize("raw", ["+998901112233", "998901112233", "90 111 22 33", "+998 (90) 111-22-33", "901112233"])
def test_phone_formats_are_normalised(raw):
    form = StudentForm(data(phone=raw))
    assert form.is_valid(), form.errors
    assert form.cleaned_data["phone"] == "+998901112233"


@pytest.mark.parametrize("raw", ["abc", "123", "+99890111", "+9989011122334", "", "++++"])
def test_bad_phone_is_rejected(raw):
    form = StudentForm(data(phone=raw))
    assert not form.is_valid() and "phone" in form.errors


def test_guardian_phone_is_normalised_and_validated():
    ok = GuardianForm({"full_name": "Ota", "phone": "90 123 45 67"})
    assert ok.is_valid() and ok.cleaned_data["phone"] == "+998901234567"
    assert not GuardianForm({"full_name": "Ota", "phone": "xyz"}).is_valid()


def test_duplicate_same_name_and_phone_is_blocked_with_a_hint():
    Student.objects.create(first_name="Ali", last_name="Valiyev", phone="+998901112233")
    form = StudentForm(data(first_name="ali", last_name="VALIYEV", phone="90 111 22 33"))
    assert not form.is_valid()
    assert form.duplicate_of is not None
    assert "allaqachon bor" in str(form.non_field_errors())


def test_duplicate_can_be_confirmed_explicitly():
    Student.objects.create(first_name="Ali", last_name="Valiyev", phone="+998901112233")
    assert StudentForm(data(allow_duplicate="on")).is_valid()


def test_siblings_with_one_phone_are_not_duplicates():
    Student.objects.create(first_name="Ali", last_name="Valiyev", phone="+998901112233")
    assert StudentForm(data(first_name="Vali")).is_valid()


def test_editing_student_does_not_clash_with_itself():
    s = Student.objects.create(first_name="Ali", last_name="Valiyev", phone="+998901112233")
    assert StudentForm(data(school="Maktab 5"), instance=s).is_valid()


def test_create_view_shows_confirmation_checkbox_on_duplicate(client, admin_user):
    Student.objects.create(first_name="Ali", last_name="Valiyev", phone="+998901112233")
    client.force_login(admin_user)
    response = client.post(reverse("students:student_create"), data())
    assert response.status_code == 200
    assert 'name="allow_duplicate"' in response.content.decode()
    assert Student.objects.count() == 1
    confirmed = client.post(reverse("students:student_create"), data(allow_duplicate="on"))
    assert confirmed.status_code == 302 and Student.objects.count() == 2


def test_create_view_saves_normalised_phone(client, admin_user):
    client.force_login(admin_user)
    client.post(reverse("students:student_create"), data(phone="90 555 44 33", first_name="Yangi"))
    assert Student.objects.get(first_name="Yangi").phone == "+998905554433"
