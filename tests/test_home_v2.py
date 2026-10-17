"""Yangi bosh sahifa: xonalar lentasi, o'ng ustun (tezkor amallar, so'nggi o'quvchilar), yon menyu."""

from datetime import date, time

import pytest
from django.urls import reverse

from apps.core.services import get_schedule_grid_data
from apps.courses.models import Course
from apps.groups.models import Enrollment, Group
from apps.schedule.models import Room, ScheduleSlot
from apps.students.models import Student
from apps.teachers.models import Teacher

pytestmark = pytest.mark.django_db


@pytest.fixture
def day():
    teacher = Teacher.objects.create(full_name="Javoxir Nazarov", phone="+998900000030")
    course = Course.objects.create(name="Rus tili", monthly_price=350000)
    group = Group.objects.create(name="Rus tili", course=course, teacher=teacher, start_date="2026-01-01", max_students=15)
    room = Room.objects.create(name="2", capacity=15)
    for i in range(3):
        st = Student.objects.create(first_name=f"Ism{i}", last_name=f"Familiya{i}", phone="")
        Enrollment.objects.create(student=st, group=group, status="active", start_date=date(2026, 10, 1))
    # 30 daqiqalik qadamga to'g'ri kelmaydigan boshlanish: eski setkada ko'rinmas edi
    slot = ScheduleSlot.objects.create(group=group, weekday=0, start_time=time(14, 15), end_time=time(16, 0), room=room)
    return {"group": group, "room": room, "slot": slot}


def test_timeline_places_off_step_lesson_with_seats(day):
    tl = get_schedule_grid_data(weekday=0, step_minutes=30)["timeline"]
    row = next(r for r in tl["rows"] if r["room"] == day["room"])
    block = row["blocks"][0]
    assert block["enrolled"] == 3 and block["capacity"] == 15
    # 08:00–21:00 = 780 daqiqa: 14:15 -> 375/780, 105 daqiqa uzunlik
    assert round(block["left"], 2) == round(375 * 100 / 780, 2)
    assert round(block["width"], 2) == round(105 * 100 / 780, 2)


def test_timeline_partial_renders_block(client, admin_user, day):
    client.force_login(admin_user)
    html = client.get(reverse("accounts:dashboard_schedule_grid"), {"weekday": 0, "step": 30}).content.decode()
    assert "14:15–16:00 Rus tili" in html and "3/15" in html and "Javoxir Nazarov" in html
    assert "15 o'rin" in html
    assert "," not in html.split('style="left: ')[1].split("%")[0]  # foiz nuqta bilan (CSS uchun)


def test_home_right_column_for_director(client, director_user, day):
    client.force_login(director_user)
    html = client.get(reverse("accounts:home")).content.decode()
    assert "Tezkor amallar" in html and "So'nggi o'quvchilar" in html
    assert "Familiya2 Ism2" in html and "Rus tili · Bugun" in html
    assert "Yangi qabul" in html and 'class="yt-side"' in html


def test_teacher_home_has_no_quick_actions_or_money(client, teacher_user, day):
    client.force_login(teacher_user)
    html = client.get(reverse("accounts:home")).content.decode()
    assert "Tezkor amallar" not in html and "So'nggi o'quvchilar" not in html
    assert "Qarzdorlar" not in html and "Yangi qabul" not in html


def test_dark_mode_toggle_and_early_script_present(client, director_user):
    client.force_login(director_user)
    html = client.get(reverse("accounts:home")).content.decode()
    assert "data-mode-toggle" in html
    # Boshlang'ich rejim sahifa chizilishidan oldin o'rnatiladi (miltillamaslik uchun): CSS dan oldin
    assert html.index("yt-mode") < html.index("css/app.css")
