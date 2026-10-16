"""O'quvchi kartasi: oylik davomat taqvimi, tarix, kartadan guruhga qo'shish."""

from datetime import date, time

import pytest
from django.urls import reverse

from apps.attendance.models import Attendance
from apps.courses.models import Course
from apps.groups.models import Enrollment, Group
from apps.schedule.models import Lesson, Room
from apps.students.card import attendance_calendar
from apps.students.models import Student
from apps.teachers.models import Teacher

pytestmark = pytest.mark.django_db


@pytest.fixture
def setup(teacher_user):
    course = Course.objects.create(name="English", monthly_price=400000)
    teacher = Teacher.objects.create(full_name="Ustoz", phone="+998900000000", user=teacher_user)
    group = Group.objects.create(name="ENG-1", course=course, teacher=teacher, start_date="2026-01-01")
    student = Student.objects.create(first_name="Ali", last_name="Valiyev", phone="+998901112233")
    enrollment = Enrollment.objects.create(student=student, group=group, status="active", start_date=date(2026, 10, 3), paid_from=date(2026, 10, 3))
    room = Room.objects.create(name="101")

    def lesson(day, status="held"):
        return Lesson.objects.create(group=group, date=date(2026, 10, day), start_time=time(9), end_time=time(11), room=room, teacher=teacher, status=status)

    before_join = lesson(1)
    present, absent, excused, _unmarked = lesson(5), lesson(7), lesson(9), lesson(12)
    future = lesson(28, status="scheduled")
    cancelled = lesson(14, status="cancelled")
    for les, status in ((present, "present"), (absent, "absent"), (excused, "excused")):
        Attendance.objects.create(lesson=les, student=student, status=status)
    return {"student": student, "enrollment": enrollment, "group": group, "before": before_join, "future": future, "cancelled": cancelled}


def test_attendance_calendar_states(setup):
    cal = attendance_calendar(setup["student"], setup["enrollment"], date(2026, 10, 1), today=date(2026, 10, 13))
    states = {d["lesson"].date.day: d["state"] for d in cal["days"]}
    assert 1 not in states  # a'zolikdan oldingi dars ko'rsatilmaydi
    assert states == {5: "present", 7: "absent", 9: "excused", 12: "unmarked", 14: "cancelled", 28: "upcoming"}
    assert (cal["present"], cal["absent"], cal["excused"], cal["unmarked"]) == (1, 1, 1, 1)


def test_card_renders_for_admin_with_tabs_and_calendar(client, admin_user, setup):
    client.force_login(admin_user)
    resp = client.get(reverse("students:student_detail", args=[setup["student"].pk]), {"month": "2026-10"})
    assert resp.status_code == 200
    html = resp.content.decode()
    assert 'data-tab="payments"' in html and 'data-tab="history"' in html
    assert "mv-att-day is-present" in html and "mv-att-day is-absent" in html
    assert resp.context["month_title"] == "Oktabr 2026"
    assert setup["group"] in resp.context["enroll_groups"]


def test_teacher_sees_calendar_but_no_finance_or_history(client, teacher_user, setup):
    client.force_login(teacher_user)
    html = client.get(reverse("students:student_detail", args=[setup["student"].pk])).content.decode()
    assert 'data-tab="payments"' not in html and 'data-tab="history"' not in html
    assert 'id="enroll-modal"' not in html


def test_enroll_from_card_returns_to_card(client, admin_user, setup):
    other = Group.objects.create(name="ENG-2", course=setup["group"].course, teacher=setup["group"].teacher, start_date="2026-01-01")
    client.force_login(admin_user)
    card = reverse("students:student_detail", args=[setup["student"].pk])
    resp = client.post(
        reverse("groups:group_enroll", args=[other.pk]),
        {"student": setup["student"].pk, "status": "trial", "start_date": "2026-10-13", "next": card},
    )
    assert resp.status_code == 302 and resp["Location"] == card
    assert Enrollment.objects.filter(student=setup["student"], group=other, status="trial").exists()
    # tarixda guruhga qo'shilish ko'rinadi
    history = client.get(card).context["history"]
    assert any("ENG-2" in item["text"] for item in history)


def test_enroll_ignores_foreign_next(client, admin_user, setup):
    other = Group.objects.create(name="ENG-3", course=setup["group"].course, teacher=setup["group"].teacher, start_date="2026-01-01")
    client.force_login(admin_user)
    resp = client.post(
        reverse("groups:group_enroll", args=[other.pk]),
        {"student": setup["student"].pk, "status": "active", "start_date": "2026-10-13", "next": "https://evil.example/x"},
    )
    assert resp["Location"] == reverse("groups:group_detail", args=[other.pk])
