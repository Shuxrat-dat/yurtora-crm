"""Arxivlash sababi va ketish sabablari hisoboti."""

import pytest
from django.urls import reverse

from apps.students.models import Student

pytestmark = pytest.mark.django_db


@pytest.fixture
def student(db):
    return Student.objects.create(first_name="Ali", last_name="Valiyev", phone="+998901112233")


def test_archive_records_reason_and_time(client, admin_user, student):
    client.force_login(admin_user)
    client.post(
        reverse("students:student_status", args=[student.pk]),
        {"status": "archived", "archive_reason": "price"},
    )
    student.refresh_from_db()
    assert student.status == "archived"
    assert student.archive_reason == "price"
    assert student.archived_at is not None


def test_invalid_reason_is_ignored_and_reactivation_clears(client, admin_user, student):
    client.force_login(admin_user)
    url = reverse("students:student_status", args=[student.pk])
    client.post(url, {"status": "archived", "archive_reason": "<script>"})
    student.refresh_from_db()
    assert student.archive_reason == ""

    client.post(url, {"status": "active"})
    student.refresh_from_db()
    assert student.archived_at is None and student.archive_reason == ""


def test_archive_toggle_view_accepts_reason(client, admin_user, student):
    client.force_login(admin_user)
    client.post(reverse("students:student_archive", args=[student.pk]), {"archive_reason": "moved"})
    student.refresh_from_db()
    assert student.archive_reason == "moved"


def test_churn_report_counts_and_access(client, admin_user, teacher_user, student):
    student.change_status("archived", "price")
    other = Student.objects.create(first_name="Vali", last_name="Aliyev", phone="+998901112244")
    other.change_status("archived", "price")
    third = Student.objects.create(first_name="Sami", last_name="Karimov", phone="+998901112255")
    third.change_status("archived", "moved")

    url = reverse("students:churn_report")
    client.force_login(teacher_user)
    assert client.get(url).status_code == 403

    client.force_login(admin_user)
    resp = client.get(url)
    assert resp.status_code == 200
    assert resp.context["total"] == 3
    assert resp.context["rows"][0] == {"label": "Narx qimmat", "count": 2, "percent": 67}
