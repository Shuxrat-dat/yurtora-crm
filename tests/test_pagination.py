"""Guruhlar va o'qituvchilar ro'yxatlari sahifalanadi."""

import pytest
from django.urls import reverse

from apps.courses.models import Course
from apps.groups.models import Group
from apps.teachers.models import Teacher

pytestmark = pytest.mark.django_db


@pytest.fixture
def many_groups(db):
    course = Course.objects.create(name="English", monthly_price=500000)
    teacher = Teacher.objects.create(full_name="Ustoz", phone="+998900000000")
    for i in range(25):
        Group.objects.create(name=f"G{i:02d}", course=course, teacher=teacher, start_date="2026-01-01")


def test_group_list_paginated(client, admin_user, many_groups):
    client.force_login(admin_user)
    url = reverse("groups:group_list")
    r1 = client.get(url)
    assert len(r1.context["groups"]) == 20
    assert r1.context["is_paginated"] is True
    r2 = client.get(url, {"page": 2, "status": "active"})
    assert len(r2.context["groups"]) == 5
    assert "status=active" in r2.context["querystring"]
    assert "page=" not in r2.context["querystring"]
    assert b"Oldingi" in r2.content


def test_teacher_list_paginated(client, admin_user):
    for i in range(23):
        Teacher.objects.create(full_name=f"Ustoz {i}", phone=f"+9989000000{i:02d}")
    client.force_login(admin_user)
    r = client.get(reverse("teachers:teacher_list"), {"page": 2})
    assert len(r.context["teachers"]) == 3
