"""Dars jadvali: oylik ko'rinish (standart), hafta va xonalar rejimlari."""

from datetime import date, time

import pytest
from django.urls import reverse

from apps.courses.models import Course
from apps.groups.models import Group
from apps.schedule.models import Lesson, Room
from apps.teachers.models import Teacher

pytestmark = pytest.mark.django_db
URL = "schedule:weekly_schedule"


@pytest.fixture
def lessons(teacher_user):
    room = Room.objects.create(name="101")
    course = Course.objects.create(name="English", monthly_price=400000)
    mine = Teacher.objects.create(full_name="Mening", phone="+998900000001", user=teacher_user)
    other = Teacher.objects.create(full_name="Boshqa", phone="+998900000002")
    g1 = Group.objects.create(name="G-MINE", course=course, teacher=mine, start_date="2026-01-01")
    g2 = Group.objects.create(name="G-OTHER", course=course, teacher=other, start_date="2026-01-01")
    for day in (5, 12, 19):
        Lesson.objects.create(group=g1, date=date(2026, 10, day), start_time=time(9), end_time=time(11), room=room, teacher=mine)
    Lesson.objects.create(group=g2, date=date(2026, 10, 7), start_time=time(14), end_time=time(16), room=room, teacher=other)
    Lesson.objects.create(group=g2, date=date(2026, 11, 2), start_time=time(14), end_time=time(16), room=room, teacher=other)


def total(resp, month):
    return sum(len(v) for d, v in resp.context["lessons_by_day"].items() if d.month == month)


def test_month_is_default_view(client, admin_user):
    client.force_login(admin_user)
    resp = client.get(reverse(URL))
    assert resp.context["view_mode"] == "month"
    weeks = resp.context["month_weeks"]
    assert all(len(w) == 7 for w in weeks) and weeks[0][0].weekday() == 0


def test_month_counts_and_navigation(client, admin_user, lessons):
    client.force_login(admin_user)
    resp = client.get(reverse(URL), {"month": "2026-10"})
    assert resp.context["month_total"] == 4
    assert resp.context["month_title"] == "Oktabr 2026"
    assert resp.context["prev_month"] == "2026-09" and resp.context["next_month"] == "2026-11"
    assert b"G-MINE" in resp.content and b"G-OTHER" in resp.content


def test_month_filter_and_garbage_param(client, admin_user, lessons):
    client.force_login(admin_user)
    group = Group.objects.get(name="G-OTHER")
    resp = client.get(reverse(URL), {"month": "2026-10", "group": group.pk})
    assert resp.context["month_total"] == 1
    resp = client.get(reverse(URL), {"month": "garbage"})
    assert resp.status_code == 200 and resp.context["view_mode"] == "month"


def test_teacher_sees_only_own_lessons_in_month(client, teacher_user, lessons):
    client.force_login(teacher_user)
    resp = client.get(reverse(URL), {"month": "2026-10"})
    assert resp.context["month_total"] == 3
    assert b"G-OTHER" not in resp.content


def test_week_and_rooms_modes(client, admin_user, lessons):
    client.force_login(admin_user)
    resp = client.get(reverse(URL), {"week": "2026-10-05"})
    assert resp.context["view_mode"] == "week"
    assert total(resp, 10) == 2  # 5 va 7 oktabr
    resp = client.get(reverse(URL), {"view": "rooms"})
    assert resp.context["view_mode"] == "rooms"
    assert reverse("accounts:dashboard_schedule_grid").encode() in resp.content
