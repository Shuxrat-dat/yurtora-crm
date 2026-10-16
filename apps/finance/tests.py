"""Tests for finance services and models."""

import threading
from datetime import date, timedelta
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from apps.courses.models import Course
from apps.finance.models import LedgerEntry
from apps.finance.services import (
    accrue_lesson_charges,
    add_adjustment,
    get_balance,
    get_debtors,
    grant_excused_credit,
    register_payment,
)
from apps.groups.models import Enrollment, Group
from apps.schedule.models import Lesson, Room
from apps.students.models import Student
from apps.teachers.models import Teacher

User = get_user_model()


class LedgerEntryImmutabilityTestCase(TestCase):
    """Test that LedgerEntry records cannot be modified or deleted."""

    def setUp(self):
        """Set up test data."""
        self.director = User.objects.create_user(
            username="director", email="director@test.uz", password="test", role="director"
        )
        self.teacher_user = User.objects.create_user(
            username="teacher", email="teacher@test.uz", password="test", role="teacher"
        )
        self.teacher = Teacher.objects.create(
            user=self.teacher_user,
            full_name="Teacher",
            phone="+998901111111",
        )
        self.course = Course.objects.create(
            name="Test Course", level="A1", monthly_price=400000
        )
        self.group = Group.objects.create(
            name="Test Group",
            course=self.course,
            teacher=self.teacher,
            max_students=10,
            start_date=timezone.now().date(),
        )
        self.student = Student.objects.create(
            first_name="Test", last_name="Student", phone="+998902222222"
        )

    def test_cannot_update_existing_entry(self):
        """Test that existing entry cannot be updated via save()."""
        entry = register_payment(
            student=self.student,
            group=self.group,
            amount=100000,
            method=LedgerEntry.PaymentMethod.CASH,
            user=self.director,
            entry_date=timezone.now().date(),
        )

        # Try to modify
        entry.amount = 200000
        with self.assertRaises(ValidationError) as ctx:
            entry.save()
        self.assertIn("o'zgartirib bo'lmaydi", str(ctx.exception).lower())

    def test_cannot_delete_entry(self):
        """Test that entry cannot be deleted."""
        entry = register_payment(
            student=self.student,
            group=self.group,
            amount=100000,
            method=LedgerEntry.PaymentMethod.CASH,
            user=self.director,
            entry_date=timezone.now().date(),
        )

        with self.assertRaises(ValidationError) as ctx:
            entry.delete()
        self.assertIn("o'chirib bo'lmaydi", str(ctx.exception).lower())

    def test_queryset_update_raises_error(self):
        """Test that queryset.update() raises error."""
        register_payment(
            student=self.student,
            group=self.group,
            amount=100000,
            method=LedgerEntry.PaymentMethod.CASH,
            user=self.director,
            entry_date=timezone.now().date(),
        )

        with self.assertRaises(ValidationError) as ctx:
            LedgerEntry.objects.filter(student=self.student).update(amount=200000)
        self.assertIn("o'zgartirib bo'lmaydi", str(ctx.exception).lower())

    def test_queryset_delete_raises_error(self):
        """Test that queryset.delete() raises error."""
        register_payment(
            student=self.student,
            group=self.group,
            amount=100000,
            method=LedgerEntry.PaymentMethod.CASH,
            user=self.director,
            entry_date=timezone.now().date(),
        )

        with self.assertRaises(ValidationError) as ctx:
            LedgerEntry.objects.filter(student=self.student).delete()
        self.assertIn("o'chirib bo'lmaydi", str(ctx.exception).lower())


class AccrueLessonChargesTestCase(TestCase):
    """Test accrue_lesson_charges service."""

    def setUp(self):
        """Set up test data."""
        self.teacher_user = User.objects.create_user(
            username="teacher", email="teacher@test.uz", password="test", role="teacher"
        )
        self.teacher = Teacher.objects.create(
            user=self.teacher_user, full_name="Teacher", phone="+998901111111"
        )
        self.course = Course.objects.create(
            name="Test Course", level="A1", monthly_price=400000
        )
        self.group = Group.objects.create(
            name="Test Group",
            course=self.course,
            teacher=self.teacher,
            max_students=10,
            start_date=timezone.now().date(),
        )
        self.room = Room.objects.create(name="Room 1")
        self.student1 = Student.objects.create(
            first_name="Ali", last_name="Aliyev", phone="+998901111111"
        )
        self.student2 = Student.objects.create(
            first_name="Baha", last_name="Bahaev", phone="+998902222222"
        )

    def test_accrue_idempotent(self):
        """Test that repeated accrue_lesson_charges does not create duplicates."""
        enrollment = Enrollment.objects.create(
            student=self.student1,
            group=self.group,
            status=Enrollment.Status.ACTIVE,
            paid_from=timezone.now().date(),
        )

        lesson = Lesson.objects.create(
            group=self.group,
            date=timezone.now().date(),
            start_time="10:00",
            end_time="11:30",
            room=self.room,
            teacher=self.teacher,
            status=Lesson.Status.HELD,
        )

        # First call
        entries1 = accrue_lesson_charges(lesson)
        self.assertEqual(len(entries1), 1)

        # Second call should not create new entries
        entries2 = accrue_lesson_charges(lesson)
        self.assertEqual(len(entries2), 0)

        # Check database
        total_charges = LedgerEntry.objects.filter(
            enrollment=enrollment, lesson=lesson, kind=LedgerEntry.Kind.CHARGE
        ).count()
        self.assertEqual(total_charges, 1)

    def test_no_charge_for_trial(self):
        """Test that trial enrollments are not charged."""
        Enrollment.objects.create(
            student=self.student1,
            group=self.group,
            status=Enrollment.Status.TRIAL,
            paid_from=None,
        )

        lesson = Lesson.objects.create(
            group=self.group,
            date=timezone.now().date(),
            start_time="10:00",
            end_time="11:30",
            room=self.room,
            teacher=self.teacher,
            status=Lesson.Status.HELD,
        )

        entries = accrue_lesson_charges(lesson)
        self.assertEqual(len(entries), 0)

    def test_no_charge_for_frozen(self):
        """Test that frozen enrollments are not charged."""
        Enrollment.objects.create(
            student=self.student1,
            group=self.group,
            status=Enrollment.Status.FROZEN,
            paid_from=timezone.now().date() - timedelta(days=10),
            frozen_from=timezone.now().date() - timedelta(days=5),
        )

        lesson = Lesson.objects.create(
            group=self.group,
            date=timezone.now().date(),
            start_time="10:00",
            end_time="11:30",
            room=self.room,
            teacher=self.teacher,
            status=Lesson.Status.HELD,
        )

        entries = accrue_lesson_charges(lesson)
        self.assertEqual(len(entries), 0)

    def test_no_charge_for_finished(self):
        """Test that finished enrollments are not charged."""
        Enrollment.objects.create(
            student=self.student1,
            group=self.group,
            status=Enrollment.Status.FINISHED,
            start_date=timezone.now().date() - timedelta(days=30),
            paid_from=timezone.now().date() - timedelta(days=30),
            end_date=timezone.now().date() - timedelta(days=5),
        )

        lesson = Lesson.objects.create(
            group=self.group,
            date=timezone.now().date(),
            start_time="10:00",
            end_time="11:30",
            room=self.room,
            teacher=self.teacher,
            status=Lesson.Status.HELD,
        )

        entries = accrue_lesson_charges(lesson)
        self.assertEqual(len(entries), 0)

    def test_no_charge_before_paid_from(self):
        """Test that no charge is created for lessons before paid_from date."""
        Enrollment.objects.create(
            student=self.student1,
            group=self.group,
            status=Enrollment.Status.ACTIVE,
            paid_from=timezone.now().date() + timedelta(days=10),
        )

        lesson = Lesson.objects.create(
            group=self.group,
            date=timezone.now().date(),
            start_time="10:00",
            end_time="11:30",
            room=self.room,
            teacher=self.teacher,
            status=Lesson.Status.HELD,
        )

        entries = accrue_lesson_charges(lesson)
        self.assertEqual(len(entries), 0)

    def test_october_2026_partial_month_charges(self):
        """
        Test October 2026: group meets Tue/Thu/Sat (14 lessons),
        monthly_price=400000, student paid_from=2026-10-21.
        Should only charge for lessons from 2026-10-22 onwards.
        """
        # October 2026 starts on Thursday (2026-10-01)
        # Tue/Thu/Sat: 3,6,8,10,13,15,17,20,22,24,27,29,31 = 13 days
        # Actually: Oct 1-31: Thu 1,3, Sat 3, Tue 6, Thu 8, Sat 10, Tue 13, Thu 15, Sat 17,
        #           Tue 20, Thu 22, Sat 24, Tue 27, Thu 29, Sat 31
        # Let's use actual dates for Oct 2026:
        # Oct 2026: Thu 1, Sat 3, Tue 6, Thu 8, Sat 10, Tue 13, Thu 15, Sat 17,
        #           Tue 20, Thu 22, Sat 24, Tue 27, Thu 29, Sat 31
        # Total: 14 lessons

        lesson_dates = [
            date(2026, 10, 1),   # Thu
            date(2026, 10, 3),   # Sat
            date(2026, 10, 6),   # Tue
            date(2026, 10, 8),   # Thu
            date(2026, 10, 10),  # Sat
            date(2026, 10, 13),  # Tue
            date(2026, 10, 15),  # Thu
            date(2026, 10, 17),  # Sat
            date(2026, 10, 20),  # Tue
            date(2026, 10, 22),  # Thu
            date(2026, 10, 24),  # Sat
            date(2026, 10, 27),  # Tue
            date(2026, 10, 29),  # Thu
            date(2026, 10, 31),  # Sat
        ]

        enrollment = Enrollment.objects.create(
            student=self.student1,
            group=self.group,
            status=Enrollment.Status.ACTIVE,
            paid_from=date(2026, 10, 21),
        )

        # Create lessons
        lessons = []
        for lesson_date in lesson_dates:
            lesson = Lesson.objects.create(
                group=self.group,
                date=lesson_date,
                start_time="10:00",
                end_time="11:30",
                room=self.room,
                teacher=self.teacher,
                status=Lesson.Status.HELD,
            )
            lessons.append(lesson)

        # Accrue charges for each lesson
        for lesson in lessons:
            accrue_lesson_charges(lesson)

        # Check: only lessons from 10-22 onwards should be charged (9 lessons)
        charged_lessons = LedgerEntry.objects.filter(
            enrollment=enrollment, kind=LedgerEntry.Kind.CHARGE
        ).count()
        expected = len([d for d in lesson_dates if d >= date(2026, 10, 22)])
        self.assertEqual(charged_lessons, expected)

    def test_full_month_equals_monthly_price(self):
        """Test that charges for a full month sum to exactly monthly_price."""
        # October 2026: 14 lessons, 400000
        lesson_dates = [
            date(2026, 10, 1), date(2026, 10, 3), date(2026, 10, 6),
            date(2026, 10, 8), date(2026, 10, 10), date(2026, 10, 13),
            date(2026, 10, 15), date(2026, 10, 17), date(2026, 10, 20),
            date(2026, 10, 22), date(2026, 10, 24), date(2026, 10, 27),
            date(2026, 10, 29), date(2026, 10, 31),
        ]

        enrollment = Enrollment.objects.create(
            student=self.student1,
            group=self.group,
            status=Enrollment.Status.ACTIVE,
            paid_from=date(2026, 10, 1),
        )

        held_lessons = []
        for lesson_date in lesson_dates:
            lesson = Lesson.objects.create(
                group=self.group,
                date=lesson_date,
                start_time="10:00",
                end_time="11:30",
                room=self.room,
                teacher=self.teacher,
                status=Lesson.Status.HELD,
            )
            held_lessons.append(lesson)
        # Oy darslari oldindan yaratiladi (generate_lessons), so'ng o'tkaziladi
        for lesson in held_lessons:
            accrue_lesson_charges(lesson)

        charges_count = LedgerEntry.objects.filter(
            enrollment=enrollment, kind=LedgerEntry.Kind.CHARGE
        ).count()
        self.assertEqual(charges_count, 14, f"Expected 14 charges, got {charges_count}")

        total_charges = LedgerEntry.objects.filter(
            enrollment=enrollment, kind=LedgerEntry.Kind.CHARGE
        ).aggregate(total=models_sum("amount"))["total"] or 0

        self.assertEqual(total_charges, -400000)

    def test_lessons_accrued_in_arbitrary_order(self):
        """Test that lessons accrued in arbitrary order still sum to monthly_price."""
        import random

        lesson_dates = [
            date(2026, 10, 1), date(2026, 10, 3), date(2026, 10, 6),
            date(2026, 10, 8), date(2026, 10, 10), date(2026, 10, 13),
            date(2026, 10, 15), date(2026, 10, 17), date(2026, 10, 20),
            date(2026, 10, 22), date(2026, 10, 24), date(2026, 10, 27),
            date(2026, 10, 29), date(2026, 10, 31),
        ]

        enrollment = Enrollment.objects.create(
            student=self.student1,
            group=self.group,
            status=Enrollment.Status.ACTIVE,
            paid_from=date(2026, 10, 1),
        )

        # Создаём все уроки сначала
        lessons = []
        for lesson_date in lesson_dates:
            lesson = Lesson.objects.create(
                group=self.group,
                date=lesson_date,
                start_time="10:00",
                end_time="11:30",
                room=self.room,
                teacher=self.teacher,
                status=Lesson.Status.HELD,
            )
            lessons.append(lesson)

        # Начисляем в произвольном порядке
        shuffled_lessons = lessons.copy()
        random.shuffle(shuffled_lessons)

        for lesson in shuffled_lessons:
            accrue_lesson_charges(lesson)

        total_charges = LedgerEntry.objects.filter(
            enrollment=enrollment, kind=LedgerEntry.Kind.CHARGE
        ).aggregate(total=models_sum("amount"))["total"] or 0

        self.assertEqual(total_charges, -400000)

    def test_n14_p400000_sum_equals_p(self):
        """Test n=14 lessons, P=400000, sum exactly equals P."""
        lesson_dates = [
            date(2026, 11, 2), date(2026, 11, 4), date(2026, 11, 6),
            date(2026, 11, 9), date(2026, 11, 11), date(2026, 11, 13),
            date(2026, 11, 16), date(2026, 11, 18), date(2026, 11, 20),
            date(2026, 11, 23), date(2026, 11, 25), date(2026, 11, 27),
            date(2026, 11, 28), date(2026, 11, 30),
        ]

        enrollment = Enrollment.objects.create(
            student=self.student1,
            group=self.group,
            status=Enrollment.Status.ACTIVE,
            paid_from=date(2026, 11, 1),
        )

        held_lessons = []
        for lesson_date in lesson_dates:
            lesson = Lesson.objects.create(
                group=self.group,
                date=lesson_date,
                start_time="10:00",
                end_time="11:30",
                room=self.room,
                teacher=self.teacher,
                status=Lesson.Status.HELD,
            )
            held_lessons.append(lesson)
        # Oy darslari oldindan yaratiladi (generate_lessons), so'ng o'tkaziladi
        for lesson in held_lessons:
            accrue_lesson_charges(lesson)

        total_charges = LedgerEntry.objects.filter(
            enrollment=enrollment, kind=LedgerEntry.Kind.CHARGE
        ).aggregate(total=models_sum("amount"))["total"] or 0

        self.assertEqual(total_charges, -400000)

    def test_payment_covers_full_month(self):
        """Test that payment of 400000 after full month results in zero balance."""
        lesson_dates = [
            date(2026, 10, 1), date(2026, 10, 3), date(2026, 10, 6),
            date(2026, 10, 8), date(2026, 10, 10), date(2026, 10, 13),
            date(2026, 10, 15), date(2026, 10, 17), date(2026, 10, 20),
            date(2026, 10, 22), date(2026, 10, 24), date(2026, 10, 27),
            date(2026, 10, 29), date(2026, 10, 31),
        ]

        Enrollment.objects.create(
            student=self.student1,
            group=self.group,
            status=Enrollment.Status.ACTIVE,
            paid_from=date(2026, 10, 1),
        )

        director = User.objects.create_user(
            username="director", email="director@test.uz", password="test", role="director"
        )

        held_lessons = []
        for lesson_date in lesson_dates:
            lesson = Lesson.objects.create(
                group=self.group,
                date=lesson_date,
                start_time="10:00",
                end_time="11:30",
                room=self.room,
                teacher=self.teacher,
                status=Lesson.Status.HELD,
            )
            held_lessons.append(lesson)
        # Oy darslari oldindan yaratiladi (generate_lessons), so'ng o'tkaziladi
        for lesson in held_lessons:
            accrue_lesson_charges(lesson)

        # Make payment
        register_payment(
            student=self.student1,
            group=self.group,
            amount=400000,
            method=LedgerEntry.PaymentMethod.CASH,
            user=director,
            entry_date=date(2026, 10, 31),
        )

        balance = get_balance(self.student1, self.group)
        self.assertEqual(balance, 0)


def models_sum(field):
    """Helper for Sum import."""
    from django.db.models import Sum
    return Sum(field)


class ExcusedCreditTestCase(TestCase):
    """Test excused_credit service."""

    def setUp(self):
        """Set up test data."""
        self.director = User.objects.create_user(
            username="director", email="director@test.uz", password="test", role="director"
        )
        self.admin = User.objects.create_user(
            username="admin", email="admin@test.uz", password="test", role="administrator"
        )
        self.teacher_user = User.objects.create_user(
            username="teacher", email="teacher@test.uz", password="test", role="teacher"
        )
        self.teacher = Teacher.objects.create(
            user=self.teacher_user, full_name="Teacher", phone="+998901111111"
        )
        self.course = Course.objects.create(
            name="Test Course", level="A1", monthly_price=400000
        )
        self.group = Group.objects.create(
            name="Test Group",
            course=self.course,
            teacher=self.teacher,
            max_students=10,
            start_date=timezone.now().date(),
        )
        self.room = Room.objects.create(name="Room 1")
        self.student = Student.objects.create(
            first_name="Test", last_name="Student", phone="+998901111111"
        )
        self.enrollment = Enrollment.objects.create(
            student=self.student,
            group=self.group,
            status=Enrollment.Status.ACTIVE,
            paid_from=timezone.now().date(),
        )

        self.lesson = Lesson.objects.create(
            group=self.group,
            date=timezone.now().date(),
            start_time="10:00",
            end_time="11:30",
            room=self.room,
            teacher=self.teacher,
            status=Lesson.Status.HELD,
        )

    def test_excused_credit_requires_comment(self):
        """Test that excused_credit requires a comment."""
        accrue_lesson_charges(self.lesson)
        charge = LedgerEntry.objects.get(
            enrollment=self.enrollment, lesson=self.lesson, kind=LedgerEntry.Kind.CHARGE
        )

        with self.assertRaises(ValidationError) as ctx:
            grant_excused_credit(charge, self.director, "")
        self.assertIn("izoh majburiy", str(ctx.exception).lower())

    def test_excused_credit_only_once_per_charge(self):
        """Test that only one excused_credit can be granted per charge."""
        accrue_lesson_charges(self.lesson)
        charge = LedgerEntry.objects.get(
            enrollment=self.enrollment, lesson=self.lesson, kind=LedgerEntry.Kind.CHARGE
        )

        grant_excused_credit(charge, self.director, "Sababli kelmadi")

        with self.assertRaises(ValidationError) as ctx:
            grant_excused_credit(charge, self.director, "Yana sababli")
        self.assertIn("allaqachon", str(ctx.exception).lower())

    def test_excused_credit_permissions(self):
        """Test that only director can grant excused_credit (administrator is denied)."""
        accrue_lesson_charges(self.lesson)
        charge = LedgerEntry.objects.get(
            enrollment=self.enrollment, lesson=self.lesson, kind=LedgerEntry.Kind.CHARGE
        )

        # Admin NOT OK: uzrli sabab (excused_credit) faqat direktor uchun
        with self.assertRaises(PermissionDenied):
            grant_excused_credit(charge, self.admin, "Test admin")

        # Teacher NOT OK
        with self.assertRaises(PermissionDenied):
            grant_excused_credit(charge, self.teacher_user, "Test teacher")


class PermissionsTestCase(TestCase):
    """Test permission checks."""

    def setUp(self):
        """Set up test data."""
        self.director = User.objects.create_user(
            username="director", email="director@test.uz", password="test", role="director"
        )
        self.admin = User.objects.create_user(
            username="admin", email="admin@test.uz", password="test", role="administrator"
        )
        self.teacher_user = User.objects.create_user(
            username="teacher", email="teacher@test.uz", password="test", role="teacher"
        )
        self.teacher = Teacher.objects.create(
            user=self.teacher_user, full_name="Teacher", phone="+998901111111"
        )
        self.course = Course.objects.create(
            name="Test Course", level="A1", monthly_price=400000
        )
        self.group = Group.objects.create(
            name="Test Group",
            course=self.course,
            teacher=self.teacher,
            max_students=10,
            start_date=timezone.now().date(),
        )
        self.student = Student.objects.create(
            first_name="Test", last_name="Student", phone="+998901111111"
        )

    def test_teacher_cannot_register_payment(self):
        """Test that teacher cannot register payment."""
        with self.assertRaises(PermissionDenied):
            register_payment(
                student=self.student,
                group=self.group,
                amount=100000,
                method=LedgerEntry.PaymentMethod.CASH,
                user=self.teacher_user,
                entry_date=timezone.now().date(),
            )

    def test_adjustment_only_director(self):
        """Test that only director can add adjustment."""
        # Admin cannot
        with self.assertRaises(PermissionDenied):
            add_adjustment(
                student=self.student,
                group=self.group,
                amount=50000,
                user=self.admin,
                comment="Test adjustment",
            )

        # Director can
        adjustment = add_adjustment(
            student=self.student,
            group=self.group,
            amount=-50000,
            user=self.director,
            comment="Test adjustment",
        )
        self.assertEqual(adjustment.amount, -50000)


class DebtorsTestCase(TestCase):
    """Test get_debtors service."""

    def setUp(self):
        """Set up test data."""
        self.director = User.objects.create_user(
            username="director", email="director@test.uz", password="test", role="director"
        )
        self.teacher_user = User.objects.create_user(
            username="teacher", email="teacher@test.uz", password="test", role="teacher"
        )
        self.teacher = Teacher.objects.create(
            user=self.teacher_user, full_name="Teacher", phone="+998901111111"
        )
        self.course = Course.objects.create(
            name="Test Course", level="A1", monthly_price=400000
        )
        self.group = Group.objects.create(
            name="Test Group",
            course=self.course,
            teacher=self.teacher,
            max_students=10,
            start_date=timezone.now().date(),
        )
        self.room = Room.objects.create(name="Room 1")

        self.student1 = Student.objects.create(
            first_name="Ali", last_name="Aliyev", phone="+998901111111"
        )
        self.student2 = Student.objects.create(
            first_name="Baha", last_name="Bahaev", phone="+998902222222"
        )
        self.student3 = Student.objects.create(
            first_name="Davron", last_name="Davronov", phone="+998903333333"
        )

    def test_get_debtors_returns_correct_students(self):
        """Test that get_debtors returns students with negative balance."""
        # Student1: debt
        Enrollment.objects.create(
            student=self.student1,
            group=self.group,
            status=Enrollment.Status.ACTIVE,
            paid_from=timezone.now().date(),
        )
        lesson1 = Lesson.objects.create(
            group=self.group,
            date=timezone.now().date(),
            start_time="10:00",
            end_time="11:30",
            room=self.room,
            teacher=self.teacher,
            status=Lesson.Status.HELD,
        )
        accrue_lesson_charges(lesson1)

        # Student2: paid
        Enrollment.objects.create(
            student=self.student2,
            group=self.group,
            status=Enrollment.Status.ACTIVE,
            paid_from=timezone.now().date(),
        )
        lesson2 = Lesson.objects.create(
            group=self.group,
            date=timezone.now().date() + timedelta(days=1),
            start_time="10:00",
            end_time="11:30",
            room=self.room,
            teacher=self.teacher,
            status=Lesson.Status.HELD,
        )
        accrue_lesson_charges(lesson2)
        register_payment(
            student=self.student2,
            group=self.group,
            amount=50000,
            method=LedgerEntry.PaymentMethod.CASH,
            user=self.director,
            entry_date=timezone.now().date(),
        )

        # Student3: no charges
        Enrollment.objects.create(
            student=self.student3,
            group=self.group,
            status=Enrollment.Status.TRIAL,
            paid_from=None,
        )

        debtors = get_debtors(self.group)

        # Only student1 and possibly student2 (if balance < 0)
        debtor_students = [d["student"] for d in debtors]
        self.assertIn(self.student1, debtor_students)
        self.assertNotIn(self.student3, debtor_students)

        # Check balance is negative
        for debtor in debtors:
            self.assertLess(debtor["balance"], 0)


@skipUnless(
    connection.vendor == "postgresql",
    "Concurrent test requires PostgreSQL",
)
class ConcurrentAccrueTestCase(TransactionTestCase):
    """Test concurrent accrue_lesson_charges on PostgreSQL."""

    def setUp(self):
        """Set up test data."""
        self.teacher_user = User.objects.create_user(
            username="teacher", email="teacher@test.uz", password="test", role="teacher"
        )
        self.teacher = Teacher.objects.create(
            user=self.teacher_user, full_name="Teacher", phone="+998901111111"
        )
        self.course = Course.objects.create(
            name="Test Course", level="A1", monthly_price=400000
        )
        self.group = Group.objects.create(
            name="Test Group",
            course=self.course,
            teacher=self.teacher,
            max_students=10,
            start_date=timezone.now().date(),
        )
        self.room = Room.objects.create(name="Room 1")
        self.student = Student.objects.create(
            first_name="Test", last_name="Student", phone="+998901111111"
        )
        # Ikkinchi o'quvchi: freeze testlari uchun alohida zachislik
        # (bitta o'quvchi bitta guruhda faqat bitta ochiq a'zolikka ega bo'la oladi)
        self.student1 = Student.objects.create(
            first_name="Second", last_name="Student", phone="+998902222222"
        )
        self.enrollment = Enrollment.objects.create(
            student=self.student,
            group=self.group,
            status=Enrollment.Status.ACTIVE,
            paid_from=timezone.now().date(),
        )
        self.lesson = Lesson.objects.create(
            group=self.group,
            date=timezone.now().date(),
            start_time="10:00",
            end_time="11:30",
            room=self.room,
            teacher=self.teacher,
            status=Lesson.Status.HELD,
        )

    def test_concurrent_accrue_creates_only_one_charge(self):
        """Test that concurrent accrue_lesson_charges creates only one charge."""
        results = []
        errors = []

        def try_accrue():
            try:
                entries = accrue_lesson_charges(self.lesson)
                results.append(len(entries))
            except Exception as e:
                errors.append(str(e))

        thread1 = threading.Thread(target=try_accrue)
        thread2 = threading.Thread(target=try_accrue)

        thread1.start()
        thread2.start()

        thread1.join()
        thread2.join()

        # One should create, one should find existing (0 created)
        total_attempts = len(results) + len(errors)
        self.assertEqual(total_attempts, 2)

        # Check database: exactly one charge
        charges = LedgerEntry.objects.filter(
            enrollment=self.enrollment, lesson=self.lesson, kind=LedgerEntry.Kind.CHARGE
        ).count()
        self.assertEqual(charges, 1)

    def test_no_charge_for_lesson_during_freeze_period(self):
        """Test that lesson during freeze period is not charged even if marked held after unfreeze."""
        # Студент активен с 1 октября, заморожен с 10 октября
        enrollment = Enrollment.objects.create(
            student=self.student1,
            group=self.group,
            status=Enrollment.Status.ACTIVE,
            paid_from=date(2026, 10, 1),
            frozen_from=date(2026, 10, 10),
        )

        # Урок 12 октября (во время заморозки), но отмечен проведённым позже
        lesson = Lesson.objects.create(
            group=self.group,
            date=date(2026, 10, 12),
            start_time="10:00",
            end_time="11:30",
            room=self.room,
            teacher=self.teacher,
            status=Lesson.Status.HELD,
        )

        # Начисление не должно произойти, т.к. lesson.date >= frozen_from.
        # setUp dagi boshqa o'quvchi (self.student) ushbu darsga to'lov olishi mumkin,
        # shuning uchun tekshiruv faqat muzlatilgan a'zolik bo'yicha.
        entries = accrue_lesson_charges(lesson)
        self.assertFalse(any(e.enrollment_id == enrollment.pk for e in entries))

        charges = LedgerEntry.objects.filter(
            enrollment=enrollment, lesson=lesson, kind=LedgerEntry.Kind.CHARGE
        ).count()
        self.assertEqual(charges, 0)

    def test_charge_for_lesson_before_freeze(self):
        """Test that lesson before freeze period IS charged."""
        # Студент активен с 1 октября, заморожен с 15 октября
        enrollment = Enrollment.objects.create(
            student=self.student1,
            group=self.group,
            status=Enrollment.Status.ACTIVE,
            paid_from=date(2026, 10, 1),
            frozen_from=date(2026, 10, 15),
        )

        # Урок 10 октября (до заморозки)
        lesson = Lesson.objects.create(
            group=self.group,
            date=date(2026, 10, 10),
            start_time="10:00",
            end_time="11:30",
            room=self.room,
            teacher=self.teacher,
            status=Lesson.Status.HELD,
        )

        # Начисление должно произойти (для muzlashdan oldingi a'zolik)
        entries = accrue_lesson_charges(lesson)
        self.assertTrue(any(e.enrollment_id == enrollment.pk for e in entries))

        charges = LedgerEntry.objects.filter(
            enrollment=enrollment, lesson=lesson, kind=LedgerEntry.Kind.CHARGE
        ).count()
        self.assertEqual(charges, 1)
