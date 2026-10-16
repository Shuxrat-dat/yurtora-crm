"""Guruhlar orasida ko'chirish."""

from datetime import date

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from apps.courses.models import Course
from apps.finance import services as finance
from apps.groups import services as groups
from apps.groups.models import Enrollment, EnrollmentLog, Group
from apps.students.models import Student
from apps.teachers.models import Teacher

pytestmark = pytest.mark.django_db
DAY = date(2026, 3, 10)


@pytest.fixture
def world():
    teacher = Teacher.objects.create(full_name="Ustoz", phone="+998900000000")
    course = Course.objects.create(name="English", monthly_price=400000)
    a = Group.objects.create(name="A", course=course, teacher=teacher, start_date=date(2026, 1, 1), max_students=20)
    b = Group.objects.create(name="B", course=course, teacher=teacher, start_date=date(2026, 1, 1), max_students=1)
    student = Student.objects.create(first_name="Ali", last_name="Valiyev", phone="+998901112233")
    Enrollment.objects.create(student=student, group=a, status="active", start_date=date(2026, 3, 1), paid_from=date(2026, 3, 1))
    return {"a": a, "b": b, "student": student}


def test_transfer_moves_enrollment(admin_user, world):
    new = groups.transfer_student(world["student"], world["a"], world["b"], admin_user, start_date=DAY)
    old = Enrollment.objects.get(student=world["student"], group=world["a"])
    assert old.status == "finished" and old.end_date == date(2026, 3, 9)
    assert new.group == world["b"] and new.status == "active" and new.start_date == DAY
    assert EnrollmentLog.objects.filter(enrollment=new, action="transfer").exists()


def test_trial_stays_trial(admin_user, world):
    Enrollment.objects.filter(student=world["student"]).update(status="trial", paid_from=None)
    assert groups.transfer_student(world["student"], world["a"], world["b"], admin_user, start_date=DAY).status == "trial"


def test_full_target_group_rolls_everything_back(admin_user, world):
    other = Student.objects.create(first_name="B", last_name="B", phone="+998900000009")
    groups.enroll_student(other, world["b"], start_date=DAY)
    with pytest.raises(ValidationError):
        groups.transfer_student(world["student"], world["a"], world["b"], admin_user, start_date=DAY)
    assert Enrollment.objects.get(student=world["student"], group=world["a"]).status == "active"


def test_same_group_rejected(admin_user, world):
    with pytest.raises(ValidationError):
        groups.transfer_student(world["student"], world["a"], world["a"], admin_user, start_date=DAY)


def test_carry_balance_director_only(director_user, admin_user, world):
    finance.register_payment(world["student"], world["a"], 300000, "cash", director_user, DAY)
    with pytest.raises(PermissionDenied):
        groups.transfer_student(world["student"], world["a"], world["b"], admin_user, start_date=DAY, carry_balance=True)
    groups.transfer_student(world["student"], world["a"], world["b"], director_user, start_date=DAY, carry_balance=True)
    assert finance.get_balance(world["student"], world["a"]) == 0
    assert finance.get_balance(world["student"], world["b"]) == 300000


def test_transfer_view_access_and_flow(client, admin_user, teacher_user, world):
    url = reverse("groups:group_transfer", args=[world["a"].pk, world["student"].pk])
    client.force_login(teacher_user)
    assert client.get(url).status_code == 403
    client.force_login(admin_user)
    assert client.get(url).status_code == 200
    response = client.post(url, {"to_group": world["b"].pk, "start_date": "2026-03-10"})
    assert response.status_code == 302
    assert Enrollment.objects.filter(student=world["student"], group=world["b"], status="active").exists()
