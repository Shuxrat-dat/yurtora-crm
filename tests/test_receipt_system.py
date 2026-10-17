"""Tests for the receipt system and receipt settings (Chek sozlamalari)."""

from datetime import date

import pytest
from django.urls import reverse

from apps.courses.models import Course
from apps.finance.models import LedgerEntry, ReceiptSettings
from apps.finance.services import generate_receipt_qr_svg
from apps.groups.models import Enrollment, Group
from apps.students.models import Student
from apps.teachers.models import Teacher

pytestmark = pytest.mark.django_db


@pytest.fixture
def receipt_world(director_user):
    teacher = Teacher.objects.create(full_name="Avazbek ustoz", phone="+998901234567")
    course = Course.objects.create(name="Ingliz tili-1", monthly_price=1000000)
    group = Group.objects.create(
        name="Ingliz tili-1",
        course=course,
        teacher=teacher,
        start_date="2026-01-01",
    )
    student = Student.objects.create(
        first_name="John", last_name="Doe", phone="+998909998877"
    )
    enrollment = Enrollment.objects.create(
        student=student, group=group, status="active", start_date=date(2026, 9, 1)
    )
    entry = LedgerEntry.objects.create(
        student=student,
        group=group,
        enrollment=enrollment,
        kind=LedgerEntry.Kind.PAYMENT,
        amount=100000,
        method=LedgerEntry.PaymentMethod.CASH,
        entry_date=date(2026, 10, 14),
        comment="Test to'lov",
        created_by=director_user,
    )
    return {
        "teacher": teacher,
        "course": course,
        "group": group,
        "student": student,
        "entry": entry,
    }


def test_receipt_settings_defaults_and_singleton():
    settings = ReceiptSettings.get_settings()
    # yangi markaz uchun bo'sh: o'z ma'lumotlarini "Chek sozlamalari"da kiritadi
    assert settings.center_name == "O'quv markazi"
    assert settings.address == ""
    assert settings.phone == ""
    assert settings.footer_text == ""
    assert settings.logo_position == ReceiptSettings.LogoPosition.TOP
    assert settings.show_logo is True
    assert settings.show_header is True
    assert settings.show_qr_code is True

    # Calling get_settings again returns the same record
    settings2 = ReceiptSettings.get_settings()
    assert settings.pk == settings2.pk


def test_generate_receipt_qr_svg():
    svg = generate_receipt_qr_svg("https://demo.yurtora.uz/receipt/1/")
    assert "<svg" in svg
    assert "</svg>" in svg
    assert "<path" in svg

    empty_svg = generate_receipt_qr_svg("")
    assert empty_svg == ""


def test_receipt_settings_access_control(client, director_user, admin_user, teacher_user):
    url = reverse("finance:receipt_settings")

    # Anonymous -> login
    response = client.get(url)
    assert response.status_code == 302
    assert "login" in response.url

    # Teacher -> 403 Forbidden
    client.force_login(teacher_user)
    response = client.get(url)
    assert response.status_code == 403

    # Administrator -> 200 OK
    client.force_login(admin_user)
    response = client.get(url)
    assert response.status_code == 200
    assert "Chek Sozlamalari" in response.content.decode("utf-8")

    # Director -> 200 OK
    client.force_login(director_user)
    response = client.get(url)
    assert response.status_code == 200


def test_receipt_settings_update_post(client, director_user):
    client.force_login(director_user)
    url = reverse("finance:receipt_settings")

    data = {
        "center_name": "Ilm Academy",
        "address": "Toshkent sh. Chilonzor 12",
        "phone": "+998712000000",
        "footer_text": "Rahmat!",
        "logo_position": "bottom",
        "show_logo": "on",
        "show_header": "on",
        "show_address": "on",
        # show_phone is not submitted -> False
        "show_issued_time": "on",
        "show_payment_id": "on",
        "show_student_info": "on",
        "show_group_info": "on",
        "show_payment_method": "on",
        "show_payment_date": "on",
        "show_cashier": "on",
        "show_amount": "on",
        "show_footer": "on",
        "show_footer_text": "on",
        "show_qr_code": "on",
        "show_teacher": "on",
        "show_course_price": "on",
    }

    response = client.post(url, data, follow=True)
    assert response.status_code == 200

    settings = ReceiptSettings.get_settings()
    assert settings.center_name == "Ilm Academy"
    assert settings.address == "Toshkent sh. Chilonzor 12"
    assert settings.phone == "+998712000000"
    assert settings.footer_text == "Rahmat!"
    assert settings.logo_position == "bottom"
    assert settings.show_phone is False
    assert settings.show_address is True


def test_payment_receipt_view_renders_with_settings(client, director_user, receipt_world):
    client.force_login(director_user)
    entry = receipt_world["entry"]
    url = reverse("finance:receipt", kwargs={"entry_id": entry.pk})

    settings = ReceiptSettings.get_settings()
    settings.center_name = "Ilm Academy"
    settings.phone = "+998901112233"
    settings.show_phone = True
    settings.save()

    response = client.get(url)
    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "TO'LOV KVITANSIYASI" in content
    assert "Doe John" in content
    assert "Avazbek ustoz" in content
    assert "+998901112233" in content
    assert "Ilm Academy" in content  # markaz nomi chekda (logotip)
    assert "<svg" in content  # QR code present

    # Turn off phone and turn off QR code in settings
    settings = ReceiptSettings.get_settings()
    settings.show_phone = False
    settings.show_qr_code = False
    settings.footer_text = "Rahmat, kutib qolamiz!"
    settings.save()

    response = client.get(url)
    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "+998901112233" not in content
    assert "Rahmat, kutib qolamiz!" in content
