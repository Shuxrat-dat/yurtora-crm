"""Tests for schedule services."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.courses.models import Course
from apps.groups.models import Enrollment, Group
from apps.schedule.models import Lesson, Room
from apps.schedule.services import mark_lesson_held
from apps.students.models import Student
from apps.teachers.models import Teacher

User = get_user_model()


class MarkLessonHeldTestCase(TestCase):
    """Test mark_lesson_held service."""

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
        self.other_teacher_user = User.objects.create_user(
            username="other_teacher", email="other@test.uz", password="test", role="teacher"
        )

        self.teacher = Teacher.objects.create(
            user=self.teacher_user,
            full_name="Teacher One",
            phone="+998901111111",
        )
        self.other_teacher = Teacher.objects.create(
            user=self.other_teacher_user,
            full_name="Teacher Two",
            phone="+998902222222",
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

    def test_mark_lesson_held_creates_charges(self):
        """Test that mark_lesson_held creates charges for active enrollments."""
        enrollment = Enrollment.objects.create(
            student=self.student,
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
            status=Lesson.Status.SCHEDULED,
        )

        mark_lesson_held(lesson, self.director)

        # Check status changed
        lesson.refresh_from_db()
        self.assertEqual(lesson.status, Lesson.Status.HELD)

        # Check charges created
        from apps.finance.models import LedgerEntry

        charges = LedgerEntry.objects.filter(
            enrollment=enrollment,
            lesson=lesson,
            kind=LedgerEntry.Kind.CHARGE,
        )
        self.assertEqual(charges.count(), 1)

    def test_mark_lesson_held_idempotent(self):
        """Test that calling mark_lesson_held twice doesn't create duplicates."""
        Enrollment.objects.create(
            student=self.student,
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
            status=Lesson.Status.SCHEDULED,
        )

        # First call
        mark_lesson_held(lesson, self.director)

        # Second call (lesson already held)
        lesson.status = Lesson.Status.SCHEDULED  # reset for test
        lesson.save()
        mark_lesson_held(lesson, self.director)

        # Check only one charge exists
        from apps.finance.models import LedgerEntry

        charges_count = LedgerEntry.objects.filter(
            lesson=lesson,
            kind=LedgerEntry.Kind.CHARGE,
        ).count()
        self.assertEqual(charges_count, 1)

    def test_no_charge_for_trial(self):
        """Test that trial enrollments don't get charged when lesson marked held."""
        Enrollment.objects.create(
            student=self.student,
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
            status=Lesson.Status.SCHEDULED,
        )

        mark_lesson_held(lesson, self.director)

        # Check no charges created
        from apps.finance.models import LedgerEntry

        charges_count = LedgerEntry.objects.filter(
            lesson=lesson,
            kind=LedgerEntry.Kind.CHARGE,
        ).count()
        self.assertEqual(charges_count, 0)

    def test_no_charge_for_frozen(self):
        """Test that frozen enrollments don't get charged when lesson marked held."""
        Enrollment.objects.create(
            student=self.student,
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
            status=Lesson.Status.SCHEDULED,
        )

        mark_lesson_held(lesson, self.director)

        # Check no charges created
        from apps.finance.models import LedgerEntry

        charges_count = LedgerEntry.objects.filter(
            lesson=lesson,
            kind=LedgerEntry.Kind.CHARGE,
        ).count()
        self.assertEqual(charges_count, 0)

    def test_cannot_mark_cancelled_as_held(self):
        """Test that cancelled lessons cannot be marked as held."""
        lesson = Lesson.objects.create(
            group=self.group,
            date=timezone.now().date(),
            start_time="10:00",
            end_time="11:30",
            room=self.room,
            teacher=self.teacher,
            status=Lesson.Status.CANCELLED,
        )

        with self.assertRaises(ValidationError) as ctx:
            mark_lesson_held(lesson, self.director)
        self.assertIn("bekor qilingan", str(ctx.exception).lower())

    def test_cannot_mark_future_lesson_as_held(self):
        """Test that future lessons cannot be marked as held."""
        future_date = timezone.now().date() + timedelta(days=5)
        lesson = Lesson.objects.create(
            group=self.group,
            date=future_date,
            start_time="10:00",
            end_time="11:30",
            room=self.room,
            teacher=self.teacher,
            status=Lesson.Status.SCHEDULED,
        )

        with self.assertRaises(ValidationError) as ctx:
            mark_lesson_held(lesson, self.director)
        self.assertIn("kelajakdagi", str(ctx.exception).lower())

    def test_teacher_can_mark_own_group_lesson(self):
        """Test that teacher can mark their own group's lesson as held."""
        lesson = Lesson.objects.create(
            group=self.group,
            date=timezone.now().date(),
            start_time="10:00",
            end_time="11:30",
            room=self.room,
            teacher=self.teacher,
            status=Lesson.Status.SCHEDULED,
        )

        mark_lesson_held(lesson, self.teacher_user)

        lesson.refresh_from_db()
        self.assertEqual(lesson.status, Lesson.Status.HELD)

    def test_teacher_cannot_mark_other_group_lesson(self):
        """Test that teacher cannot mark another group's lesson as held."""
        other_group = Group.objects.create(
            name="Other Group",
            course=self.course,
            teacher=self.other_teacher,
            max_students=10,
            start_date=timezone.now().date(),
        )

        lesson = Lesson.objects.create(
            group=other_group,
            date=timezone.now().date(),
            start_time="10:00",
            end_time="11:30",
            room=self.room,
            teacher=self.other_teacher,
            status=Lesson.Status.SCHEDULED,
        )

        with self.assertRaises(PermissionDenied):
            mark_lesson_held(lesson, self.teacher_user)

    def test_admin_can_mark_any_lesson(self):
        """Test that admin can mark any lesson as held."""
        lesson = Lesson.objects.create(
            group=self.group,
            date=timezone.now().date(),
            start_time="10:00",
            end_time="11:30",
            room=self.room,
            teacher=self.teacher,
            status=Lesson.Status.SCHEDULED,
        )

        mark_lesson_held(lesson, self.admin)

        lesson.refresh_from_db()
        self.assertEqual(lesson.status, Lesson.Status.HELD)

    def test_director_can_mark_any_lesson(self):
        """Test that director can mark any lesson as held."""
        lesson = Lesson.objects.create(
            group=self.group,
            date=timezone.now().date(),
            start_time="10:00",
            end_time="11:30",
            room=self.room,
            teacher=self.teacher,
            status=Lesson.Status.SCHEDULED,
        )

        mark_lesson_held(lesson, self.director)

        lesson.refresh_from_db()
        self.assertEqual(lesson.status, Lesson.Status.HELD)
