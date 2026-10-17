"""Bosh sahifadagi "Asosiy maqsad": progress, davrlar, bitta asosiy maqsad, rol huquqlari."""

from datetime import date, datetime, time

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from apps.core.goals import goal_progress, main_goal_for, period_range
from apps.core.models import Goal
from apps.courses.models import Course
from apps.finance.models import LedgerEntry
from apps.groups.models import Enrollment, Group
from apps.students.models import Student
from apps.teachers.models import Teacher

pytestmark = pytest.mark.django_db

TODAY = date(2026, 10, 14)  # chorshanba


def student_on(d, name="A"):
    s = Student.objects.create(first_name=name, last_name="Talaba", phone="")
    Student.objects.filter(pk=s.pk).update(created_at=timezone.make_aware(datetime.combine(d, time(12))))
    return s


def test_period_ranges():
    assert period_range("day", TODAY) == (TODAY, TODAY)
    assert period_range("week", TODAY) == (date(2026, 10, 12), TODAY)
    assert period_range("month", TODAY) == (date(2026, 10, 1), TODAY)


def test_new_students_progress_by_period():
    student_on(TODAY, "A")
    student_on(date(2026, 10, 12), "B")  # shu hafta, bugun emas
    student_on(date(2026, 10, 3), "C")  # shu oy, shu hafta emas
    student_on(date(2026, 9, 30), "D")  # o'tgan oy
    goal = Goal.objects.create(metric="new_students", period="day", target=5)
    assert goal_progress(goal, TODAY)["current"] == 1
    goal.period = "week"
    assert goal_progress(goal, TODAY)["current"] == 2
    goal.period = "month"
    p = goal_progress(goal, TODAY)
    assert p["current"] == 3 and p["percent"] == 60 and not p["done"]


def test_progress_caps_at_100_and_marks_done():
    for i in range(3):
        student_on(TODAY, f"S{i}")
    p = goal_progress(Goal.objects.create(metric="new_students", period="day", target=2), TODAY)
    assert p["current"] == 3 and p["percent"] == 100 and p["done"]


def test_income_progress_counts_payments_minus_refunds(director_user):
    teacher = Teacher.objects.create(full_name="U", phone="+998900000060")
    course = Course.objects.create(name="K", monthly_price=300000)
    group = Group.objects.create(name="G", course=course, teacher=teacher, start_date="2026-01-01")
    st = student_on(date(2026, 9, 1))
    e = Enrollment.objects.create(student=st, group=group, status="active", start_date=date(2026, 9, 1))
    common = {"student": st, "group": group, "enrollment": e, "created_by": director_user}
    LedgerEntry.objects.create(kind="payment", amount=500000, method="cash", entry_date=TODAY, **common)
    LedgerEntry.objects.create(kind="refund", amount=-100000, method="cash", entry_date=TODAY, **common)
    LedgerEntry.objects.create(kind="payment", amount=999999, method="cash", entry_date=date(2026, 9, 20), **common)
    goal = Goal.objects.create(metric="income", period="month", target=1000000)
    p = goal_progress(goal, TODAY)
    assert p["current"] == 400000 and p["percent"] == 40 and p["money"]


def test_only_one_main_goal():
    a = Goal.objects.create(title="A", target=5, is_main=True)
    b = Goal.objects.create(title="B", target=7, is_main=True)
    a.refresh_from_db()
    assert not a.is_main and b.is_main
    assert Goal.objects.filter(is_main=True).count() == 1


def test_target_must_be_positive():
    with pytest.raises(IntegrityError), transaction.atomic():
        Goal.objects.create(target=0)


def test_income_goal_hidden_from_administrator(admin_user, director_user):
    Goal.objects.create(metric="income", period="month", target=1000000, is_main=True)
    assert main_goal_for(admin_user) is None
    assert main_goal_for(director_user) is not None
    Goal.objects.all().delete()
    Goal.objects.create(metric="new_students", target=5, is_main=True)
    assert main_goal_for(admin_user) is not None


def test_home_shows_main_goal_widget(client, director_user):
    Goal.objects.create(title="Yangi o'quvchilar sonini oshirish", metric="new_students", period="day", target=5, is_main=True)
    student_on(timezone.localdate())
    client.force_login(director_user)
    html = client.get(reverse("accounts:home")).content.decode()
    assert "yt-goal" in html and "asosiy maqsad" in html and "1</b>" in html and "20%" in html


def test_goal_pages_director_only(client, director_user, admin_user, teacher_user):
    for user in (admin_user, teacher_user):
        client.force_login(user)
        assert client.get(reverse("goals:goal_list")).status_code == 403
        assert client.post(reverse("goals:goal_create"), {"metric": "new_students", "period": "day", "target": "5"}).status_code == 403
    client.force_login(director_user)
    assert client.get(reverse("goals:goal_list")).status_code == 200


def test_director_creates_edits_switches_deletes(client, director_user):
    client.force_login(director_user)
    r = client.post(reverse("goals:goal_create"), {"title": "", "metric": "income", "period": "month", "target": "5 000 000", "is_main": "on"})
    assert r.status_code == 302
    goal = Goal.objects.get()
    assert goal.target == 5000000 and goal.is_main and goal.display_title == "Tushum (so'm)"
    second = Goal.objects.create(title="Ikkinchi", target=3)
    client.post(reverse("goals:goal_main", args=[second.pk]))
    goal.refresh_from_db()
    assert not goal.is_main and Goal.objects.get(pk=second.pk).is_main
    client.post(reverse("goals:goal_edit", args=[second.pk]), {"title": "Yangi nom", "metric": "new_students", "period": "week", "target": "9"})
    second.refresh_from_db()
    assert second.title == "Yangi nom" and second.target == 9 and second.period == "week"
    client.post(reverse("goals:goal_delete", args=[second.pk]))
    assert not Goal.objects.filter(pk=second.pk).exists()
    assert client.get(reverse("goals:goal_delete", args=[goal.pk])).status_code == 405  # GET bilan o'chirilmaydi


def test_invalid_target_rejected(client, director_user):
    client.force_login(director_user)
    r = client.post(reverse("goals:goal_create"), {"metric": "new_students", "period": "day", "target": "0"})
    assert r.status_code == 200 and not Goal.objects.exists()
