"""Учитель отмечает посещаемость прямо из своего профиля (блок «Bugungi darslar»)."""

from datetime import time

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.attendance.models import Attendance
from apps.courses.models import Course
from apps.groups.models import Enrollment, Group
from apps.schedule.models import Lesson, Room
from apps.students.models import Student
from apps.teachers.models import Teacher

pytestmark = pytest.mark.django_db
TODAY = timezone.localdate()


@pytest.fixture
def world(teacher_user, password):
    teacher = Teacher.objects.create(user=teacher_user, full_name="Mening Ustozim", phone="+998900000000")
    other_user = User.objects.create_user("t2", password=password, role="teacher")
    other = Teacher.objects.create(user=other_user, full_name="Boshqa Ustoz", phone="+998900000001")
    course = Course.objects.create(name="English", monthly_price=400000)
    group = Group.objects.create(name="MY-1", course=course, teacher=teacher, start_date=TODAY)
    foreign = Group.objects.create(name="OTHER-1", course=course, teacher=other, start_date=TODAY)
    room = Room.objects.create(name="101")
    active = Student.objects.create(first_name="Ali", last_name="Faol", phone="+998901112233")
    trial = Student.objects.create(first_name="Vali", last_name="Sinov", phone="+998901112244")
    left = Student.objects.create(first_name="Sami", last_name="Ketgan", phone="+998901112255")
    Enrollment.objects.create(student=active, group=group, status="active", start_date=TODAY)
    Enrollment.objects.create(student=trial, group=group, status="trial", start_date=TODAY)
    Enrollment.objects.create(student=left, group=group, status="finished", start_date=TODAY, end_date=TODAY)
    mine = Lesson.objects.create(group=group, date=TODAY, start_time=time(9), end_time=time(10), room=room, teacher=teacher)
    theirs = Lesson.objects.create(group=foreign, date=TODAY, start_time=time(11), end_time=time(12), room=room, teacher=other)
    return {"teacher": teacher, "group": group, "foreign": foreign, "mine": mine, "theirs": theirs, "active": active, "trial": trial, "left": left, "other_user": other_user}


def mark(client, lesson, student, status, group=None):
    return client.post(
        reverse("groups:group_attendance", args=[(group or lesson.group).pk]),
        {"lesson_id": lesson.pk, "student_id": student.pk, "status": status},
    )


def test_profile_lists_todays_lessons_with_students_and_buttons(client, teacher_user, world):
    client.force_login(teacher_user)
    body = client.get(reverse("teachers:my_profile")).content.decode()
    assert "Bugungi darslar" in body and "MY-1" in body
    assert "Faol Ali" in body and "Sinov Vali" in body  # пробный ученик тоже отмечается
    assert "Ketgan" not in body  # вышедший не показывается
    assert "OTHER-1" not in body  # чужой урок не показывается
    for label in ("Keldi", "Kechikdi", "Sababli", "Kelmadi"):
        assert label in body
    assert reverse("groups:group_attendance", args=[world["group"].pk]) in body
    assert reverse("schedule:mark_lesson_held", args=[world["mine"].pk]) in body


def test_teacher_marks_and_changes_attendance(client, teacher_user, world):
    client.force_login(teacher_user)
    response = mark(client, world["mine"], world["active"], "present")
    assert response.status_code == 200 and response.json()["state"] == "present"
    assert mark(client, world["mine"], world["active"], "absent").json()["state"] == "absent"
    assert Attendance.objects.get(lesson=world["mine"], student=world["active"]).status == "absent"
    assert mark(client, world["mine"], world["trial"], "late").status_code == 200  # пробный ученик


def test_current_marks_are_shown_as_pressed(client, teacher_user, world):
    client.force_login(teacher_user)
    mark(client, world["mine"], world["active"], "late")
    body = client.get(reverse("teachers:my_profile")).content.decode()
    assert "is-late is-on" in body and 'aria-pressed="true"' in body


def test_teacher_cannot_mark_foreign_group(client, teacher_user, world):
    client.force_login(teacher_user)
    foreign_student = Student.objects.create(first_name="Chet", last_name="El", phone="+998901112266")
    Enrollment.objects.create(student=foreign_student, group=world["foreign"], status="active", start_date=TODAY)
    assert mark(client, world["theirs"], foreign_student, "present").status_code == 403
    assert not Attendance.objects.exists()


def test_other_teacher_sees_only_own_lessons(client, world):
    client.force_login(world["other_user"])
    body = client.get(reverse("teachers:my_profile")).content.decode()
    assert "OTHER-1" in body and "MY-1" not in body


def test_no_lessons_today_message(client, teacher_user):
    Teacher.objects.create(user=teacher_user, full_name="Bo'sh", phone="+998900000009")
    client.force_login(teacher_user)
    assert "Bugun dars yo'q" in client.get(reverse("teachers:my_profile")).content.decode()


def test_cancelled_lesson_is_not_listed(client, teacher_user, world):
    Lesson.objects.filter(pk=world["mine"].pk).update(status="cancelled")
    client.force_login(teacher_user)
    body = client.get(reverse("teachers:my_profile")).content.decode()
    assert f'data-lesson="{world["mine"].pk}"' not in body
    assert "Bugun dars yo'q" in body


def test_director_viewing_a_teacher_profile_can_mark_too(client, director_user, world):
    client.force_login(director_user)
    body = client.get(reverse("teachers:teacher_profile", args=[world["teacher"].pk])).content.decode()
    assert "MY-1" in body
    assert mark(client, world["mine"], world["active"], "present").status_code == 200


def test_teacher_marks_lesson_held_from_profile(client, teacher_user, world):
    client.force_login(teacher_user)
    response = client.post(reverse("schedule:mark_lesson_held", args=[world["mine"].pk]))
    assert response.status_code == 302
    world["mine"].refresh_from_db()
    assert world["mine"].status == "held"
