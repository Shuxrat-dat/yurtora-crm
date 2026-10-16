"""F-02 / F-08: архив ученика и закрытие группы останавливают начисления и завершают записи."""

from datetime import time, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.courses.models import Course
from apps.finance.models import LedgerEntry
from apps.finance.services import accrue_lesson_charges, get_debtors
from apps.groups.models import Enrollment, EnrollmentLog, Group
from apps.schedule.models import Lesson, Room
from apps.schedule.services import mark_lesson_held
from apps.students.models import Student
from apps.teachers.models import Teacher

pytestmark = pytest.mark.django_db
TODAY = timezone.localdate()


@pytest.fixture
def world(admin_user):
    teacher = Teacher.objects.create(full_name="Ustoz", phone="+998900000000")
    course = Course.objects.create(name="English", monthly_price=400000)
    group = Group.objects.create(name="ENG-1", course=course, teacher=teacher, start_date=TODAY - timedelta(days=60))
    room = Room.objects.create(name="101")
    student = Student.objects.create(first_name="Ali", last_name="Valiyev", phone="+998901112233")
    enrollment = Enrollment.objects.create(
        student=student, group=group, status="active", start_date=TODAY - timedelta(days=30), paid_from=TODAY - timedelta(days=30)
    )

    def lesson(offset_days, status="scheduled", hour=9):
        return Lesson.objects.create(
            group=group, date=TODAY + timedelta(days=offset_days), start_time=time(hour), end_time=time(hour + 1),
            room=room, teacher=teacher, status=status,
        )

    return {"group": group, "student": student, "enrollment": enrollment, "lesson": lesson, "teacher": teacher, "course": course}


def charges(student):
    return LedgerEntry.objects.filter(student=student, kind="charge").count()


def test_archive_finishes_enrollment_and_stops_charges(client, admin_user, world):
    first = world["lesson"](-3, hour=9)
    mark_lesson_held(first, admin_user)
    assert charges(world["student"]) == 1

    client.force_login(admin_user)
    resp = client.post(reverse("students:student_archive", args=[world["student"].pk]), {"archive_reason": "other"})
    assert resp.status_code == 302

    enrollment = Enrollment.objects.get(pk=world["enrollment"].pk)
    assert enrollment.status == "finished" and enrollment.end_date == TODAY
    assert EnrollmentLog.objects.filter(enrollment=enrollment, action="remove").exists()

    later = world["lesson"](-1, hour=11)
    mark_lesson_held(later, admin_user)
    assert charges(world["student"]) == 1  # новое начисление не появилось


def test_archive_via_edit_form_also_finishes_enrollment(client, admin_user, world):
    client.force_login(admin_user)
    student = world["student"]
    resp = client.post(
        reverse("students:student_edit", args=[student.pk]),
        {"first_name": "Ali", "last_name": "Valiyev", "phone": student.phone, "status": "archived", "joined_date": TODAY.isoformat()},
    )
    assert resp.status_code == 302
    student.refresh_from_db()
    assert student.status == "archived" and student.archived_at is not None
    assert Enrollment.objects.get(pk=world["enrollment"].pk).status == "finished"


def test_status_view_archive_and_restore_does_not_resurrect_enrollment(client, admin_user, world):
    client.force_login(admin_user)
    url = reverse("students:student_status", args=[world["student"].pk])
    client.post(url, {"status": "archived", "archive_reason": "other"})
    assert Enrollment.objects.get(pk=world["enrollment"].pk).status == "finished"
    client.post(url, {"status": "active"})
    world["student"].refresh_from_db()
    assert world["student"].status == "active"
    assert Enrollment.objects.get(pk=world["enrollment"].pk).status == "finished"  # записывать заново нужно вручную


def test_legacy_archived_student_with_active_enrollment_is_not_charged(admin_user, world):
    Student.objects.filter(pk=world["student"].pk).update(status="archived")  # как в старых данных
    lesson = world["lesson"](-2, status="held")
    assert accrue_lesson_charges(lesson) == []
    assert charges(world["student"]) == 0
    assert all(d["student"].pk != world["student"].pk for d in get_debtors())


def test_closing_group_finishes_enrollments_and_cancels_future_lessons(client, admin_user, world):
    held = world["lesson"](-3, status="held", hour=9)
    future = world["lesson"](2, hour=9)
    far = world["lesson"](20, hour=11)
    client.force_login(admin_user)
    group = world["group"]
    resp = client.post(
        reverse("groups:group_edit", args=[group.pk]),
        {
            "name": group.name, "course": world["course"].pk, "teacher": world["teacher"].pk, "max_students": 15,
            "start_date": group.start_date.isoformat(), "status": "closed",
        },
    )
    assert resp.status_code == 302
    group.refresh_from_db()
    assert group.status == "closed"
    assert Enrollment.objects.get(pk=world["enrollment"].pk).status == "finished"
    for lesson in (future, far):
        lesson.refresh_from_db()
        assert lesson.status == "cancelled"
    held.refresh_from_db()
    assert held.status == "held"  # прошедший урок не трогаем


def test_editing_active_group_does_not_touch_enrollments(client, admin_user, world):
    client.force_login(admin_user)
    group = world["group"]
    future = world["lesson"](2)
    client.post(
        reverse("groups:group_edit", args=[group.pk]),
        {
            "name": "ENG-1 renamed", "course": world["course"].pk, "teacher": world["teacher"].pk, "max_students": 15,
            "start_date": group.start_date.isoformat(), "status": "active",
        },
    )
    assert Enrollment.objects.get(pk=world["enrollment"].pk).status == "active"
    future.refresh_from_db()
    assert future.status == "scheduled"
