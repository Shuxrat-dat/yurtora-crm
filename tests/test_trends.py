"""Bosh sahifa kartalari: o'tgan oyga nisbatan o'zgarish (strelkalar)."""

from datetime import date, datetime, time

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.core.trends import dashboard_trends, same_day_last_month
from apps.courses.models import Course
from apps.finance.models import LedgerEntry
from apps.groups.models import Enrollment, EnrollmentLog, Group
from apps.students.models import Student
from apps.teachers.models import Teacher

pytestmark = pytest.mark.django_db

TODAY = date(2026, 10, 17)  # solishtiriladigan kun: 2026-09-17


def at(d):
    return timezone.make_aware(datetime.combine(d, time(12, 0)))


def enroll(student, group, start, status="active", logs=()):
    e = Enrollment.objects.create(student=student, group=group, status=status, start_date=start)
    for old, new, when in logs:
        log = EnrollmentLog.objects.create(enrollment=e, action="test", old_status=old, new_status=new)
        EnrollmentLog.objects.filter(pk=log.pk).update(changed_at=at(when))
    return e


@pytest.fixture
def world():
    teacher = Teacher.objects.create(full_name="Ustoz", phone="+998900000040")
    course = Course.objects.create(name="Matematika", monthly_price=300000)
    group = Group.objects.create(name="Mat-1", course=course, teacher=teacher, start_date=date(2026, 8, 1))
    s = [Student.objects.create(first_name=f"I{i}", last_name=f"F{i}", phone="") for i in range(4)]
    return {"group": group, "s": s}


def test_same_day_last_month_clamps_short_months():
    assert same_day_last_month(date(2026, 3, 31)) == date(2026, 2, 28)
    assert same_day_last_month(date(2026, 1, 15)) == date(2025, 12, 15)


def test_students_and_frozen_restored_from_history(world):
    g, s = world["group"], world["s"]
    enroll(s[0], g, date(2026, 9, 1), logs=[(None, "active", date(2026, 9, 1))])
    enroll(s[1], g, date(2026, 10, 5), logs=[(None, "active", date(2026, 10, 5))])  # yangi: bu oy
    # O'tgan oy faol edi, bu oy muzlatildi: o'quvchi soni o'zgarmaydi, muzlatilgan +1
    enroll(s[2], g, date(2026, 9, 1), status="frozen", logs=[(None, "active", date(2026, 9, 1)), ("active", "frozen", date(2026, 10, 10))])
    # O'tgan oy faol edi, bu oy ketdi
    e = enroll(s[3], g, date(2026, 9, 1), status="finished", logs=[(None, "active", date(2026, 9, 1)), ("active", "finished", date(2026, 10, 3))])
    Enrollment.objects.filter(pk=e.pk).update(end_date=date(2026, 10, 3))

    t = dashboard_trends(today=TODAY)
    assert t["active_students"]["delta"] == 0  # +1 yangi, -1 ketgan
    assert t["frozen_count"]["delta"] == 1 and t["frozen_count"]["state"] == "bad"
    # Yangi qabul: 1–17 sentabr 3 ta, 1–17 oktabr 1 ta
    assert t["new_enrollments"]["delta"] == -2 and t["new_enrollments"]["state"] == "bad"
    assert t["left_students"]["delta"] == 1 and t["left_students"]["state"] == "bad"
    assert t["active_students"]["state"] == "flat"


def test_debt_and_income_trends(world, director_user):
    g, s = world["group"], world["s"]
    e = enroll(s[0], g, date(2026, 9, 1))
    common = {"student": s[0], "group": g, "enrollment": e, "created_by": director_user}
    LedgerEntry.objects.create(kind="charge", amount=-300000, entry_date=date(2026, 9, 5), **common)
    LedgerEntry.objects.create(kind="payment", amount=100000, method="cash", entry_date=date(2026, 9, 10), **common)
    LedgerEntry.objects.create(kind="payment", amount=150000, method="cash", entry_date=date(2026, 10, 12), **common)

    t = dashboard_trends(director_user, today=TODAY)
    # Qarz: 17.09 da 200 000, bugun 50 000 -> 150 000 kamaydi (yaxshi)
    assert t["total_debt"]["delta"] == -150000 and t["total_debt"]["state"] == "good"
    assert t["debtors_count"]["delta"] == 0
    # Tushum: 1–17 oktabr 150 000, 1–17 sentabr 100 000 -> +50%
    assert t["monthly_income"]["percent"] == 50 and t["monthly_income"]["state"] == "good"


def test_administrator_gets_no_money_trends(world, admin_user):
    t = dashboard_trends(admin_user, today=TODAY)
    assert "total_debt" not in t and "monthly_income" not in t and "debtors_count" not in t
    assert "active_students" in t


def test_home_shows_trends(client, director_user, world):
    enroll(world["s"][0], world["group"], timezone.localdate(), logs=[(None, "active", timezone.localdate())])
    client.force_login(director_user)
    html = client.get(reverse("accounts:home")).content.decode()
    assert "o&#x27;tgan oyga nisbatan" in html or "o'tgan oyga nisbatan" in html
    assert 'class="yt-trend is-good"' in html
