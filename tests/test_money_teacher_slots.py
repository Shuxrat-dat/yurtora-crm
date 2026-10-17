"""Summalar bo'shliq bilan, o'qituvchi faqat o'z guruhlarini ko'radi, slot bir nechta kunga."""

from datetime import date

import pytest
from django.urls import reverse

from apps.core.filters import parse_int
from apps.courses.forms import CourseForm
from apps.courses.models import Course
from apps.finance.forms import PaymentForm
from apps.groups.models import Enrollment, Group
from apps.schedule.models import Room, ScheduleSlot
from apps.students.models import Student
from apps.teachers.models import Teacher

pytestmark = pytest.mark.django_db


# ---------- Summalar ----------
def test_payment_form_accepts_spaced_amount():
    form = PaymentForm({"amount": "1 500 000", "method": "cash", "entry_date": "2026-10-17"})
    form.is_valid()
    assert "amount" not in form.errors
    assert form.cleaned_data["amount"] == 1500000


def test_course_price_spaced_and_rendered_with_spaces():
    form = CourseForm({"name": "Matematika", "level": "", "monthly_price": "350 000", "is_active": "on"})
    assert form.is_valid(), form.errors
    course = form.save()
    assert course.monthly_price == 350000
    html = str(CourseForm(instance=course)["monthly_price"])
    assert 'value="350 000"' in html and "data-money" in html and 'inputmode="numeric"' in html


def test_parse_int_ignores_spaces():
    assert parse_int("2 000 000", 1) == 2000000
    assert parse_int("12 500") == 12500
    assert parse_int("abc") is None


# ---------- O'qituvchi faqat o'z guruhlarini ko'radi ----------
@pytest.fixture
def two_teachers(teacher_user):
    me = Teacher.objects.create(full_name="Izzat", phone="+998900000010", user=teacher_user)
    other = Teacher.objects.create(full_name="Javoxir", phone="+998900000011")
    course = Course.objects.create(name="Fan", monthly_price=300000)
    mine = Group.objects.create(name="Matematika-A", course=course, teacher=me, start_date="2026-10-01")
    theirs = Group.objects.create(name="Rus-tili-B", course=course, teacher=other, start_date="2026-10-01")
    both = Student.objects.create(first_name="Aziz", last_name="Hamedullayev", phone="")
    stranger = Student.objects.create(first_name="Begona", last_name="Talaba", phone="")
    Enrollment.objects.create(student=both, group=mine, status="active", start_date=date(2026, 10, 1))
    Enrollment.objects.create(student=both, group=theirs, status="active", start_date=date(2026, 10, 1))
    Enrollment.objects.create(student=stranger, group=theirs, status="active", start_date=date(2026, 10, 1))
    return {"both": both, "stranger": stranger}


def test_teacher_student_list_hides_other_teachers_groups(client, teacher_user, two_teachers):
    client.force_login(teacher_user)
    html = client.get(reverse("students:student_list")).content.decode()
    assert "Hamedullayev" in html and "Matematika-A" in html
    assert "Rus-tili-B" not in html
    assert "Begona" not in html


def test_teacher_student_card_hides_other_teachers_groups(client, teacher_user, two_teachers):
    client.force_login(teacher_user)
    html = client.get(reverse("students:student_detail", args=[two_teachers["both"].pk])).content.decode()
    assert "Matematika-A" in html and "Rus-tili-B" not in html
    assert client.get(reverse("students:student_detail", args=[two_teachers["stranger"].pk])).status_code == 403


# ---------- Slot: bir nechta hafta kuni ----------
@pytest.fixture
def slot_world():
    teacher = Teacher.objects.create(full_name="Ustoz", phone="+998900000020")
    course = Course.objects.create(name="English", monthly_price=400000)
    group = Group.objects.create(name="ENG-1", course=course, teacher=teacher, start_date="2026-01-01")
    return {"group": group, "room": Room.objects.create(name="101")}


def _slot_post(world, days, **extra):
    data = {"group": world["group"].pk, "weekdays": days, "start_time": "14:00", "end_time": "16:00", "room": world["room"].pk, "is_active": "on"}
    data.update(extra)
    return data


def test_slot_create_many_weekdays(client, admin_user, slot_world):
    client.force_login(admin_user)
    response = client.post(reverse("schedule:slot_create"), _slot_post(slot_world, ["0", "2", "4"]))
    assert response.status_code == 302
    assert sorted(ScheduleSlot.objects.values_list("weekday", flat=True)) == [0, 2, 4]


def test_slot_create_clash_on_one_day_creates_nothing(client, admin_user, slot_world):
    ScheduleSlot.objects.create(group=slot_world["group"], weekday=2, start_time="15:00", end_time="17:00", room=slot_world["room"])
    client.force_login(admin_user)
    response = client.post(reverse("schedule:slot_create"), _slot_post(slot_world, ["0", "2"]))
    assert response.status_code == 200
    assert "Chorshanba" in response.content.decode()
    assert ScheduleSlot.objects.count() == 1


def test_slot_create_requires_a_weekday(client, admin_user, slot_world):
    client.force_login(admin_user)
    response = client.post(reverse("schedule:slot_create"), _slot_post(slot_world, []))
    assert response.status_code == 200 and not ScheduleSlot.objects.exists()


def test_slot_create_page_shows_weekday_checkboxes(client, admin_user, slot_world):
    client.force_login(admin_user)
    html = client.get(reverse("schedule:slot_create")).content.decode()
    assert html.count('name="weekdays"') == 7 and 'type="checkbox"' in html
