"""Yuqori paneldagi umumiy qidiruv va qo'ng'iroqcha (eslatmalar): rollar bo'yicha huquqlar."""

from datetime import date, datetime, time

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.core.alerts import build_alerts
from apps.core.search import global_search
from apps.courses.models import Course
from apps.finance.models import LedgerEntry
from apps.groups.models import Enrollment, Group
from apps.schedule.models import Lesson, Room
from apps.students.models import Student
from apps.teachers.models import Teacher

pytestmark = pytest.mark.django_db


@pytest.fixture
def center(teacher_user, director_user):
    me = Teacher.objects.create(full_name="Izzat Ustoz", phone="+998900000050", user=teacher_user)
    other = Teacher.objects.create(full_name="Nodira Opa", phone="+998900000051")
    course = Course.objects.create(name="Matematika", monthly_price=300000)
    mine = Group.objects.create(name="Mat-A", course=course, teacher=me, start_date="2026-01-01")
    theirs = Group.objects.create(name="Bio-B", course=course, teacher=other, start_date="2026-01-01")
    aziz = Student.objects.create(first_name="Aziz", last_name="Hamedullayev", phone="+998911112233")
    begona = Student.objects.create(first_name="Aziza", last_name="Begona", phone="+998944445566")
    alone = Student.objects.create(first_name="Yolg'iz", last_name="Guruhsiz", phone="")
    e = Enrollment.objects.create(student=aziz, group=mine, status="active", start_date=date(2026, 9, 1))
    Enrollment.objects.create(student=begona, group=theirs, status="trial", start_date=date(2026, 10, 1))
    return {"me": me, "other": other, "mine": mine, "theirs": theirs, "aziz": aziz, "begona": begona, "alone": alone, "enr": e}


# ---------- Qidiruv ----------
def test_short_query_returns_nothing(director_user):
    assert global_search(director_user, "a") is None


def test_director_finds_students_groups_teachers(director_user, center):
    r = global_search(director_user, "aziz")
    assert {s.last_name for s in r["students"]} == {"Hamedullayev", "Begona"}
    assert global_search(director_user, "Nodira")["teachers"][0].full_name == "Nodira Opa"
    assert [g.name for g in global_search(director_user, "Bio")["groups"]] == ["Bio-B"]
    assert global_search(director_user, "4445566")["students"][0].last_name == "Begona"  # telefon


def test_teacher_sees_only_own_students_and_groups(teacher_user, center):
    r = global_search(teacher_user, "aziz")
    assert [s.last_name for s in r["students"]] == ["Hamedullayev"]
    assert r["teachers"] == []
    assert global_search(teacher_user, "Bio")["groups"] == []
    assert global_search(teacher_user, "1112233")["students"] == []  # telefon bo'yicha emas


def test_accountant_searches_only_students(center, password):
    accountant = User.objects.create_user("acc", password=password, role=User.Role.ACCOUNTANT)
    r = global_search(accountant, "Mat")
    assert r["groups"] == [] and r["teachers"] == []


def test_search_endpoint_renders_sections(client, director_user, center):
    client.force_login(director_user)
    html = client.get(reverse("accounts:global_search"), {"q": "aziz"}).content.decode()
    assert "O&#x27;quvchilar" in html or "O'quvchilar" in html
    assert "Hamedullayev Aziz" in html and "Mat-A" in html
    assert client.get(reverse("accounts:global_search"), {"q": "x"}).content.decode().strip() == ""


# ---------- Eslatmalar ----------
NOW = timezone.make_aware(datetime.combine(date(2026, 10, 17), time(16, 0)))


def keys(alerts):
    return {a["key"] for a in alerts}


def test_director_alerts(director_user, center):
    LedgerEntry.objects.create(student=center["aziz"], group=center["mine"], enrollment=center["enr"], kind="charge",
                               amount=-300000, entry_date=date(2026, 10, 1), created_by=director_user)
    room = Room.objects.create(name="1")
    Lesson.objects.create(group=center["mine"], date=NOW.date(), start_time=time(9), end_time=time(11), room=room, teacher=center["me"])
    Lesson.objects.create(group=center["theirs"], date=NOW.date(), start_time=time(17), end_time=time(18), room=room, teacher=center["other"])
    Student.objects.filter(pk=center["aziz"].pk).update(birth_date=date(2012, 10, 17))

    alerts = build_alerts(director_user, now=NOW)
    assert keys(alerts) == {"debt", "unmarked", "trial", "birthday", "no_group"}
    texts = {a["key"]: a["text"] for a in alerts}
    assert texts["unmarked"] == "1 ta darsda davomat belgilanmagan"  # 17:00 dagi dars hali tugamagan
    assert texts["no_group"] == "1 ta o'quvchi guruhga qo'shilmagan"


def test_administrator_has_no_debt_alert(admin_user, center, director_user):
    LedgerEntry.objects.create(student=center["aziz"], group=center["mine"], enrollment=center["enr"], kind="charge",
                               amount=-300000, entry_date=date(2026, 10, 1), created_by=director_user)
    assert "debt" not in keys(build_alerts(admin_user, now=NOW))


def test_teacher_alerts_only_for_own_lessons(teacher_user, center):
    room = Room.objects.create(name="1")
    Lesson.objects.create(group=center["theirs"], date=NOW.date(), start_time=time(9), end_time=time(11), room=room, teacher=center["other"])
    alerts = build_alerts(teacher_user, now=NOW)
    assert "unmarked" not in keys(alerts) and "trial" not in keys(alerts) and "no_group" not in keys(alerts)


def test_bell_endpoint_shows_badge_and_nogroup_filter(client, director_user, center):
    client.force_login(director_user)
    html = client.get(reverse("accounts:alerts")).content.decode()
    assert 'class="yt-badge"' in html and "?nogroup=1" in html
    listing = client.get(reverse("students:student_list"), {"nogroup": "1"}).content.decode()
    assert "Guruhsiz" in listing and "Hamedullayev" not in listing


def test_trial_alert_ignores_cancelled_lessons_on_other_days(director_user, center):
    room = Room.objects.create(name="1")
    # O'tgan haftadagi bekor qilingan dars bugungi sinov darsini yashirmasligi kerak
    Lesson.objects.create(group=center["theirs"], date=date(2026, 10, 10), start_time=time(9), end_time=time(10), room=room, teacher=center["other"], status="cancelled")
    Lesson.objects.create(group=center["theirs"], date=NOW.date(), start_time=time(17), end_time=time(18), room=room, teacher=center["other"])
    assert "trial" in keys(build_alerts(director_user, now=NOW))
