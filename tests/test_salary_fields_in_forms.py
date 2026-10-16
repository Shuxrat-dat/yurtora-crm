"""Direktor guruh va o'qituvchi formalarida ish haqi maydonlarini ko'radi va saqlay oladi."""

import pytest
from django.urls import reverse

from apps.courses.models import Course
from apps.groups.models import Group
from apps.teachers.models import Teacher

pytestmark = pytest.mark.django_db


@pytest.fixture
def objs():
    course = Course.objects.create(name="English", monthly_price=400000)
    teacher = Teacher.objects.create(full_name="Ustoz", phone="+998900000000")
    group = Group.objects.create(name="ENG-1", course=course, teacher=teacher, start_date="2026-01-01")
    return course, teacher, group


def group_payload(course, teacher, **extra):
    data = {"name": "ENG-1", "course": course.pk, "teacher": teacher.pk, "max_students": 20,
            "start_date": "2026-01-01", "end_date": "", "status": "active"}
    data.update(extra)
    return data


def test_director_sees_and_saves_teacher_percent(client, director_user, objs):
    course, teacher, group = objs
    client.force_login(director_user)
    url = reverse("groups:group_edit", args=[group.pk])
    assert 'name="teacher_percent"' in client.get(url).content.decode()
    # bo'sh foiz endi ruxsat: guruh o'qituvchi profilidagi asosiy foizni oladi
    assert client.post(url, group_payload(course, teacher, teacher_percent="")).status_code == 302
    group.refresh_from_db()
    assert group.teacher_percent is None
    assert client.post(url, group_payload(course, teacher, teacher_percent=35)).status_code == 302
    group.refresh_from_db()
    assert group.teacher_percent == 35


def test_administrator_does_not_see_teacher_percent(client, admin_user, objs):
    course, teacher, group = objs
    client.force_login(admin_user)
    url = reverse("groups:group_edit", args=[group.pk])
    assert 'name="teacher_percent"' not in client.get(url).content.decode()
    assert client.post(url, group_payload(course, teacher)).status_code == 302


def test_director_edits_teacher_pay_type(client, director_user, objs):
    _, teacher, _ = objs
    client.force_login(director_user)
    url = reverse("teachers:teacher_edit", args=[teacher.pk])
    html = client.get(url).content.decode()
    assert 'name="pay_type"' in html and 'name="monthly_salary"' in html
    base = {"full_name": "Ustoz", "phone": "+998900000000", "is_active": "on", "user": ""}
    assert client.post(url, base).status_code == 200  # pay_type yo'q -> xato
    assert client.post(url, {**base, "pay_type": "fixed", "monthly_salary": 4500000}).status_code == 302
    teacher.refresh_from_db()
    assert teacher.pay_type == "fixed" and teacher.monthly_salary == 4500000


def test_administrator_does_not_see_pay_fields(client, admin_user, objs):
    _, teacher, _ = objs
    client.force_login(admin_user)
    html = client.get(reverse("teachers:teacher_edit", args=[teacher.pk])).content.decode()
    assert 'name="pay_type"' not in html
