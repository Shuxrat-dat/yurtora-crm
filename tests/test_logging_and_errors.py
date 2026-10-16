"""SEC-06/OBS-01: ошибки не глотаются молча и попадают в лог; ожидаемые случаи обрабатываются точечно."""

import io
import logging
from datetime import date

import pytest
from django.conf import settings
from django.urls import reverse

from apps.attendance import services as attendance_services
from apps.courses.models import Course
from apps.finance import services as finance_services
from apps.finance.services import generate_receipt_qr_svg
from apps.groups.models import Enrollment, Group
from apps.students import importer
from apps.students.models import Student
from apps.teachers.models import Teacher

pytestmark = pytest.mark.django_db


# ---------- OBS-01: настройка логирования ----------
def test_logging_is_configured_for_console():
    config = settings.LOGGING
    assert config["handlers"]["console"]["class"] == "logging.StreamHandler"
    assert "console" in config["root"]["handlers"]
    for name in ("apps", "django.request", "django.security"):
        assert config["loggers"][name]["handlers"] == ["console"]
    assert logging.getLogger("django.request").handlers, "dictConfig должен был применить обработчик"
    assert logging.getLogger("apps.finance.services").getEffectiveLevel() <= logging.INFO


def test_server_error_is_logged_with_traceback(client, director_user, monkeypatch, caplog):
    def boom(*args, **kwargs):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr("apps.accounts.views.get_dashboard_metrics", boom)
    client.raise_request_exception = False
    request_logger = logging.getLogger("django.request")
    request_logger.addHandler(caplog.handler)
    try:
        client.force_login(director_user)
        response = client.get(reverse("accounts:home"))
    finally:
        request_logger.removeHandler(caplog.handler)
    assert response.status_code == 500
    assert any(r.levelno >= logging.ERROR and r.exc_info and "synthetic failure" in str(r.exc_info[1]) for r in caplog.records)


# ---------- SEC-06: ожидаемые случаи точечно, реальные ошибки наружу ----------
def test_teacher_without_profile_gets_an_empty_home_page(client, teacher_user):
    client.force_login(teacher_user)  # учётка есть, карточки учителя нет
    response = client.get(reverse("accounts:home"))
    assert response.status_code == 200
    assert response.context["is_teacher"] is True and response.context["today_lessons"] == []


def test_teacher_dashboard_grid_without_profile_is_ok(client, teacher_user):
    client.force_login(teacher_user)
    assert client.get(reverse("accounts:dashboard_schedule_grid")).status_code == 200


@pytest.fixture
def student_card(admin_user):
    teacher = Teacher.objects.create(full_name="Ustoz", phone="+998900000000")
    course = Course.objects.create(name="English", monthly_price=400000)
    group = Group.objects.create(name="ENG-1", course=course, teacher=teacher, start_date="2026-01-01")
    student = Student.objects.create(first_name="Ali", last_name="Valiyev", phone="+998901112233")
    Enrollment.objects.create(student=student, group=group, status="active", start_date=date(2026, 9, 1))
    return student


def test_student_card_finance_error_is_not_hidden(client, admin_user, student_card, monkeypatch):
    """Раньше при сбое расчёта баланса страница молча показывала пустые финансы."""
    def boom(*args, **kwargs):
        raise RuntimeError("balance calculation failed")

    monkeypatch.setattr(finance_services, "get_balance", boom)
    client.force_login(admin_user)
    client.raise_request_exception = False
    assert client.get(reverse("students:student_detail", args=[student_card.pk])).status_code == 500


def test_student_card_attendance_error_is_not_hidden(client, admin_user, student_card, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("stats failed")

    monkeypatch.setattr(attendance_services, "get_attendance_stats", boom)
    client.force_login(admin_user)
    client.raise_request_exception = False
    assert client.get(reverse("students:student_detail", args=[student_card.pk])).status_code == 500


def test_student_card_still_renders_normally(client, admin_user, student_card):
    client.force_login(admin_user)
    response = client.get(reverse("students:student_detail", args=[student_card.pk]))
    assert response.status_code == 200
    assert response.context["total_balance"] == 0 and response.context["attendance_stats"] is not None


def test_import_reports_unreadable_file_instead_of_crashing():
    result = importer.import_students(io.BytesIO(b"not an xlsx file"))
    assert result.created == 0 and result.errors
    assert "o'qib bo'lmadi" in result.errors[0][1]


def test_qr_failure_returns_empty_and_logs_warning(caplog):
    service_logger = logging.getLogger("apps.finance.services")
    service_logger.addHandler(caplog.handler)
    try:
        assert generate_receipt_qr_svg("x" * 20000) == ""  # слишком длинно для QR
    finally:
        service_logger.removeHandler(caplog.handler)
    assert any("QR" in r.getMessage() for r in caplog.records)
    assert "<svg" in generate_receipt_qr_svg("https://example.com/receipt/1/")
