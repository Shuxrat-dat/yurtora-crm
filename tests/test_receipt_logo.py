"""Chek: har bir markazning o'z logotipi (rasm yoki nomning bosh harfi), Marvel qoldiqlari yo'q."""

from datetime import date

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.courses.models import Course
from apps.finance.models import LedgerEntry, ReceiptSettings
from apps.groups.models import Enrollment, Group
from apps.students.models import Student
from apps.teachers.models import Teacher

pytestmark = pytest.mark.django_db

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
SETTINGS_POST = {
    "center_name": "Turon",
    "address": "Severniy 87",
    "phone": "+998900000000",
    "footer_text": "Rahmat!",
    "logo_position": "top",
    "show_logo": "on",
    "show_header": "on",
}


@pytest.fixture
def entry(director_user):
    teacher = Teacher.objects.create(full_name="Ustoz", phone="+998901234567")
    course = Course.objects.create(name="Matematika", monthly_price=350000)
    group = Group.objects.create(name="Matematika", course=course, teacher=teacher, start_date="2026-01-01")
    student = Student.objects.create(first_name="Aziz", last_name="Hamedullayev", phone="")
    enrollment = Enrollment.objects.create(student=student, group=group, status="active", start_date=date(2026, 10, 1))
    return LedgerEntry.objects.create(
        student=student, group=group, enrollment=enrollment, kind=LedgerEntry.Kind.PAYMENT,
        amount=350000, method=LedgerEntry.PaymentMethod.CASH, entry_date=date(2026, 10, 17), created_by=director_user,
    )


def _post(client, **extra):
    return client.post(reverse("finance:receipt_settings"), {**SETTINGS_POST, **extra}, follow=True)


def test_upload_logo_shows_on_receipt(client, director_user, entry):
    client.force_login(director_user)
    _post(client, logo_file=SimpleUploadedFile("logo.png", PNG, content_type="image/png"))
    settings = ReceiptSettings.get_settings()
    assert settings.logo_data.startswith("data:image/png;base64,")
    html = client.get(reverse("finance:receipt", kwargs={"entry_id": entry.pk})).content.decode()
    assert 'class="rc-logo-img"' in html and settings.logo_data in html


def test_svg_and_fake_images_rejected(client, director_user):
    client.force_login(director_user)
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    response = _post(client, logo_file=SimpleUploadedFile("logo.png", svg, content_type="image/png"))
    assert "Faqat PNG, JPEG yoki WebP" in response.content.decode()
    assert ReceiptSettings.get_settings().logo_data == ""


def test_too_big_logo_rejected(client, director_user):
    client.force_login(director_user)
    big = PNG + b"\x00" * (200 * 1024)
    response = _post(client, logo_file=SimpleUploadedFile("logo.png", big, content_type="image/png"))
    assert "200 KB" in response.content.decode()
    assert ReceiptSettings.get_settings().logo_data == ""


def test_remove_logo_and_keep_logo_when_no_new_file(client, director_user):
    client.force_login(director_user)
    _post(client, logo_file=SimpleUploadedFile("logo.png", PNG, content_type="image/png"))
    _post(client)  # yangi fayl yo'q: eski logotip saqlanib qoladi
    assert ReceiptSettings.get_settings().logo_data.startswith("data:image/png")
    _post(client, logo_remove="on")
    assert ReceiptSettings.get_settings().logo_data == ""


def test_without_logo_receipt_uses_center_initial_not_marvel(client, director_user, entry):
    client.force_login(director_user)
    _post(client)
    html = client.get(reverse("finance:receipt", kwargs={"entry_id": entry.pk})).content.decode()
    assert '<span class="rc-logo-letter" aria-hidden="true">T</span>' in html
    assert "ACADEMY" not in html
    settings_html = client.get(reverse("finance:receipt_settings")).content.decode()
    assert "ACADEMY LC" not in settings_html and "M3 4h3.5" not in settings_html
    assert 'class="pv-logo-letter' in settings_html
