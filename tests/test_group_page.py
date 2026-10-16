"""Guruh sahifasi: oylik davomat jadvali, bir klikda belgilash, o'quvchilar ro'yxati."""

from datetime import date, time

import pytest
from django.urls import reverse

from apps.attendance.models import Attendance
from apps.courses.models import Course
from apps.groups import grid
from apps.groups.models import Enrollment, Group
from apps.schedule.models import Lesson, Room, ScheduleSlot
from apps.students.models import Student
from apps.teachers.models import Teacher

pytestmark = pytest.mark.django_db
TODAY = date(2026, 10, 13)
MONTH = date(2026, 10, 1)


@pytest.fixture
def setup(teacher_user):
    course = Course.objects.create(name="English", monthly_price=400000)
    teacher = Teacher.objects.create(full_name="Ustoz", phone="+998900000000", user=teacher_user)
    group = Group.objects.create(name="ENG-1", course=course, teacher=teacher, start_date="2026-01-01")
    room = Room.objects.create(name="101")
    ScheduleSlot.objects.create(group=group, weekday=0, start_time=time(9), end_time=time(11), room=room)
    student = Student.objects.create(first_name="Ali", last_name="Valiyev", phone="+998901112233")
    enrollment = Enrollment.objects.create(student=student, group=group, status="active", start_date=date(2026, 10, 3))

    def lesson(day, status="held", year=2026, month=10):
        return Lesson.objects.create(
            group=group, date=date(year, month, day), start_time=time(9), end_time=time(11), room=room,
            teacher=teacher, status=status,
        )

    return {
        "group": group, "student": student, "enrollment": enrollment, "lesson": lesson,
        "teacher": teacher, "room": room,
    }


def states(data):
    return {c["lesson"].date.day: c["state"] for c in data["rows"][0]["cells"]}


def test_grid_states_and_editable(setup):
    lesson = setup["lesson"]
    lesson(1)
    marked = lesson(5)
    lesson(12)
    lesson(8, "cancelled")
    lesson(28, "scheduled")
    Attendance.objects.create(lesson=marked, student=setup["student"], status="late")

    data = grid.attendance_grid(setup["group"], MONTH, TODAY)
    assert states(data) == {1: "na", 5: "late", 8: "cancelled", 12: "unmarked", 28: "upcoming"}
    cells = {c["lesson"].date.day: c for c in data["rows"][0]["cells"]}
    assert cells[5]["editable"] and cells[12]["editable"]
    assert not cells[1]["editable"] and not cells[8]["editable"] and not cells[28]["editable"]
    assert data["rows"][0]["present"] == 1  # kechikish ham qatnashgan hisoblanadi


def test_frozen_student_cells_not_editable(setup):
    marked = setup["lesson"](5)
    Attendance.objects.create(lesson=marked, student=setup["student"], status="present")
    Enrollment.objects.filter(pk=setup["enrollment"].pk).update(status="frozen")
    cell = grid.attendance_grid(setup["group"], MONTH, TODAY)["rows"][0]["cells"][0]
    assert cell["state"] == "present" and not cell["editable"]


def test_month_helpers():
    assert grid.add_months(date(2026, 12, 1), 1) == date(2027, 1, 1)
    assert grid.add_months(date(2026, 1, 1), -1) == date(2025, 12, 1)
    tabs = grid.month_tabs(MONTH, TODAY)
    assert len(tabs) == 7 and [t["active"] for t in tabs].count(True) == 1
    assert grid.month_label(MONTH) == "Oktabr 2026"


def test_attendance_endpoint_marks_and_changes(client, admin_user, setup):
    lesson = setup["lesson"](5)
    client.force_login(admin_user)
    url = reverse("groups:group_attendance", args=[setup["group"].pk])
    data = {"lesson_id": lesson.pk, "student_id": setup["student"].pk, "status": "present"}
    resp = client.post(url, data)
    assert resp.status_code == 200 and resp.json()["state"] == "present"
    resp = client.post(url, {**data, "status": "absent"})
    assert resp.json()["state"] == "absent"
    assert Attendance.objects.get(lesson=lesson, student=setup["student"]).status == "absent"


def test_attendance_endpoint_rejects_bad_requests(client, admin_user, setup):
    other_group = Group.objects.create(
        name="X", course=setup["group"].course, teacher=setup["teacher"], start_date="2026-01-01"
    )
    other_lesson = Lesson.objects.create(
        group=other_group, date=date(2026, 10, 5), start_time=time(9), end_time=time(10),
        room=setup["room"], teacher=setup["teacher"], status="held",
    )
    client.force_login(admin_user)
    url = reverse("groups:group_attendance", args=[setup["group"].pk])
    sid = setup["student"].pk

    far = setup["lesson"](1, "scheduled", year=2099, month=1)  # kelajakdagi dars
    assert client.post(url, {"lesson_id": far.pk, "student_id": sid, "status": "present"}).status_code == 400
    # boshqa guruh darsi
    assert client.post(url, {"lesson_id": other_lesson.pk, "student_id": sid, "status": "present"}).status_code == 404
    # noto'g'ri holat
    ok = setup["lesson"](6)
    assert client.post(url, {"lesson_id": ok.pk, "student_id": sid, "status": "boom"}).status_code == 400
    assert not Attendance.objects.exists()


def test_attendance_endpoint_permissions(client, setup, teacher_user, password):
    from apps.accounts.models import User

    lesson = setup["lesson"](5)
    url = reverse("groups:group_attendance", args=[setup["group"].pk])
    data = {"lesson_id": lesson.pk, "student_id": setup["student"].pk, "status": "present"}

    assert client.post(url, data).status_code == 302  # kirmagan
    stranger = User.objects.create_user(username="t2", password=password, role=User.Role.TEACHER)
    client.force_login(stranger)
    assert client.post(url, data).status_code == 403  # boshqa o'qituvchi guruhi
    client.force_login(teacher_user)
    assert client.post(url, data).status_code == 200  # o'z guruhi
    assert client.get(url).status_code == 405


def test_group_page_renders_for_roles(client, admin_user, teacher_user, setup):
    setup["lesson"](5)
    url = reverse("groups:group_detail", args=[setup["group"].pk])
    client.force_login(admin_user)
    resp = client.get(url, {"month": "2026-10"})
    body = resp.content.decode()
    assert resp.status_code == 200
    assert "Oktabr 2026" in body and "Dushanba" in body and "101" in body and "Valiyev" in body
    assert reverse("groups:group_attendance", args=[setup["group"].pk]) in body
    assert client.get(url, {"month": "garbage"}).status_code == 200  # noto'g'ri oy -> joriy oy

    client.force_login(teacher_user)
    teacher_body = client.get(url, {"month": "2026-10"}).content.decode()
    assert "+998901112233" not in teacher_body  # o'qituvchi telefonni ko'rmaydi
    assert reverse("groups:group_edit", args=[setup["group"].pk]) not in teacher_body
