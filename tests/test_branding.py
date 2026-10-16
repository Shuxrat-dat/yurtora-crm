"""Markaz nomi: bitta sozlamadan menyu, kirish sahifasi, Telegram xabari va o'qituvchi profiliga tushadi."""

import pytest
from django.urls import reverse

from apps.core.branding import DEFAULT_CENTER_NAME, center_initial, center_name
from apps.finance.models import ReceiptSettings
from apps.notifications.services import build_debt_message, help_text
from apps.students.models import Student

pytestmark = pytest.mark.django_db


def set_name(name):
    settings = ReceiptSettings.get_settings()
    settings.center_name = name
    settings.save()


def test_default_name_without_settings_row():
    assert not ReceiptSettings.objects.exists()
    assert center_name() == DEFAULT_CENTER_NAME
    assert not ReceiptSettings.objects.exists()  # o'qish yozuv yaratmaydi


def test_saving_settings_updates_name_immediately():
    assert center_name() == DEFAULT_CENTER_NAME  # keshga tushadi
    set_name("Ilm Academy")
    assert center_name() == "Ilm Academy"  # saqlash keshni tozalaydi


def test_blank_name_falls_back_to_default():
    set_name("   ")
    assert center_name() == DEFAULT_CENTER_NAME


def test_center_initial():
    assert center_initial("Ilm Academy") == "I"
    assert center_initial("«Bilim» markazi") == "B"
    assert center_initial("") == "Y"


def test_login_and_sidebar_show_center_and_product(client, director_user):
    set_name("Ilm Academy")
    login = client.get(reverse("accounts:login")).content.decode()
    assert "Ilm Academy" in login and "Yurtora" in login and "Marvel" not in login
    client.force_login(director_user)
    home = client.get(reverse("accounts:home")).content.decode()
    assert "Ilm Academy" in home and "Yurtora CRM" in home and "Marvel" not in home


def test_telegram_texts_use_center_name():
    set_name("Ilm Academy")
    student = Student.objects.create(first_name="Ali", last_name="Valiyev", phone="+998901112233")
    message = build_debt_message(student, [("ENG-1", 300000)])
    assert "«Ilm Academy»" in message and "Marvel" not in message
    assert "«Ilm Academy» boti" in help_text()
