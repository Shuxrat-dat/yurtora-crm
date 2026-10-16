"""Tests for Stage 1a: Models and Enrollment service."""

from datetime import date

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from apps.courses.models import Course
from apps.groups.models import Enrollment, Group
from apps.groups.services import enroll_student, remove_student
from apps.students.models import Student
from apps.teachers.models import Teacher


@pytest.fixture
def course(db):
    return Course.objects.create(
        name="Ingliz tili",
        level="IELTS",
        monthly_price=600000,
    )


@pytest.fixture
def teacher(db):
    return Teacher.objects.create(
        full_name="Akmal Saidov",
        phone="+998901234567",
    )


@pytest.fixture
def group(db, course, teacher):
    return Group.objects.create(
        name="IELTS-01",
        course=course,
        teacher=teacher,
        max_students=2,
        start_date=date(2026, 10, 1),
    )


@pytest.fixture
def student(db):
    return Student.objects.create(
        first_name="Ali",
        last_name="Valiyev",
        phone="+998901112233",
    )


@pytest.mark.django_db
class TestEnrollmentService:
    """Guruhga o'quvchi yozish xizmati (services.py) testlari."""

    def test_enroll_student_success(self, student, group):
        """O'quvchini guruhga muvaffaqiyatli qo'shish."""
        enrollment = enroll_student(student=student, group=group)

        assert enrollment.pk is not None
        assert enrollment.status == Enrollment.Status.ACTIVE
        assert enrollment.student == student
        assert enrollment.group == group
        assert group.active_students_count == 1

    def test_enroll_student_exceed_max_students(self, group, course, teacher):
        """Guruh to'lganda (max_students) yangi o'quvchi qo'shish taqiqlanadi."""
        s1 = Student.objects.create(first_name="S1", last_name="L1", phone="111")
        s2 = Student.objects.create(first_name="S2", last_name="L2", phone="222")
        s3 = Student.objects.create(first_name="S3", last_name="L3", phone="333")

        enroll_student(student=s1, group=group)
        enroll_student(student=s2, group=group)

        assert group.active_students_count == 2
        assert group.max_students == 2

        with pytest.raises(ValidationError, match="Guruh to'lgan"):
            enroll_student(student=s3, group=group)

        assert group.active_students_count == 2

    def test_enroll_student_duplicate_active_enrollment(self, student, group):
        """Bitta o'quvchini ayni bir guruhga ikki marta faol a'zo qilib qo'shish taqiqlanadi."""
        enroll_student(student=student, group=group)

        with pytest.raises(ValidationError, match="allaqachon faol a'zo"):
            enroll_student(student=student, group=group)

    def test_remove_student_success(self, student, group):
        """O'quvchini guruhdan chiqarish a'zolik holatini 'finished' ga o'zgartiradi."""
        enroll_student(student=student, group=group)
        assert group.active_students_count == 1

        removed_enrollment = remove_student(student=student, group=group)
        assert removed_enrollment.status == Enrollment.Status.FINISHED
        assert removed_enrollment.end_date is not None
        assert group.active_students_count == 0

    def test_database_unique_active_enrollment_constraint(self, student, group):
        """Ma'lumotlar bazasi darajasidagi UniqueConstraint tekshiruvi."""
        Enrollment.objects.create(
            student=student,
            group=group,
            status=Enrollment.Status.ACTIVE,
        )

        with pytest.raises(IntegrityError):
            Enrollment.objects.create(
                student=student,
                group=group,
                status=Enrollment.Status.ACTIVE,
            )
