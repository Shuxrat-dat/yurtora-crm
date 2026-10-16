"""Xonalar (kabinetlar): direktor va administrator qo'shadi, ro'yxatda bandlik ko'rinadi."""

from datetime import time

import pytest
from django.urls import reverse

from apps.courses.models import Course
from apps.groups.models import Group
from apps.schedule.models import Room, ScheduleSlot
from apps.teachers.models import Teacher

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("who", ["director_user", "admin_user"])
def test_staff_can_add_room_with_capacity(client, request, who):
    client.force_login(request.getfixturevalue(who))
    resp = client.post(
        reverse("schedule:room_create"),
        {"name": "305", "capacity": 16, "note": "3-qavat", "is_active": "on"},
    )
    assert resp.status_code == 302
    room = Room.objects.get(name="305")
    assert room.capacity == 16 and room.note == "3-qavat" and room.is_active


def test_teacher_cannot_add_room(client, teacher_user):
    client.force_login(teacher_user)
    assert client.get(reverse("schedule:room_list")).status_code == 403
    assert client.post(reverse("schedule:room_create"), {"name": "X"}).status_code == 403
    assert not Room.objects.exists()


def test_room_list_shows_weekly_usage(client, director_user):
    room = Room.objects.create(name="201", capacity=12)
    course = Course.objects.create(name="English", monthly_price=400000)
    teacher = Teacher.objects.create(full_name="Ustoz", phone="+998900000000")
    group = Group.objects.create(name="ENG-1", course=course, teacher=teacher, start_date="2026-01-01")
    ScheduleSlot.objects.create(group=group, weekday=0, start_time=time(9), end_time=time(11), room=room)
    ScheduleSlot.objects.create(group=group, weekday=2, start_time=time(9), end_time=time(10, 30), room=room)

    client.force_login(director_user)
    resp = client.get(reverse("schedule:room_list"))
    listed = next(r for r in resp.context["rooms"] if r.pk == room.pk)
    assert listed.weekly_hours == 3.5
    assert listed.group_names == ["ENG-1"]


def test_rooms_link_in_sidebar_for_director(client, director_user):
    client.force_login(director_user)
    html = client.get(reverse("accounts:home")).content.decode()
    assert reverse("schedule:room_list") in html
