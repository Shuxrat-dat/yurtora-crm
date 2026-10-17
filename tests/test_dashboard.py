"""Tests for dashboard home page."""

from datetime import date, time, timedelta

import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.core.branding import center_name
from apps.courses.models import Course
from apps.finance.models import LedgerEntry
from apps.groups.models import Enrollment, Group
from apps.schedule.models import Lesson, Room, ScheduleSlot
from apps.students.models import Student
from apps.teachers.models import Teacher
from apps.tenants.middleware import TENANCY_QUERIES_PER_REQUEST


@pytest.fixture
def dashboard_data(db):
    """Create test data for dashboard metrics."""
    # Create users
    director = User.objects.create_user(
        username="director", password="test123", role=User.Role.DIRECTOR
    )
    admin = User.objects.create_user(
        username="admin", password="test123", role=User.Role.ADMINISTRATOR
    )
    teacher_user = User.objects.create_user(
        username="teacher1", password="test123", role=User.Role.TEACHER
    )

    # Create teacher profile
    teacher = Teacher.objects.create(
        user=teacher_user,
        full_name="Test Teacher",
        phone="+998901234567",
        pay_type=Teacher.PayType.PERCENT,
    )

    # Create course
    course = Course.objects.create(name="Test Course", monthly_price=500000)

    # Create rooms
    room1 = Room.objects.create(name="Room 1", is_active=True)
    room2 = Room.objects.create(name="Room 2", is_active=True)

    # Create groups
    group1 = Group.objects.create(
        name="Group A",
        course=course,
        teacher=teacher,
        status=Group.Status.ACTIVE,
        teacher_percent=30,
        start_date=date.today() - timedelta(days=30),
    )
    group2 = Group.objects.create(
        name="Group B",
        course=course,
        teacher=teacher,
        status=Group.Status.ACTIVE,
        teacher_percent=30,
        start_date=date.today() - timedelta(days=20),
    )

    # Create students
    student1 = Student.objects.create(
        first_name="Student",
        last_name="One",
        phone="+998901111111",
        status=Student.Status.ACTIVE,
    )
    student2 = Student.objects.create(
        first_name="Student",
        last_name="Two",
        phone="+998902222222",
        status=Student.Status.ACTIVE,
    )
    student3 = Student.objects.create(
        first_name="Student",
        last_name="Three",
        phone="+998903333333",
        status=Student.Status.ACTIVE,
    )
    student4 = Student.objects.create(
        first_name="Student",
        last_name="Four",
        phone="+998904444444",
        status=Student.Status.ARCHIVED,
    )
    # Student with status "Muzlatilgan" (no enrollment: does not affect other counts)
    Student.objects.create(
        first_name="Student",
        last_name="Frozen",
        phone="+998906666666",
        status=Student.Status.FROZEN,
    )

    # Create enrollments with different statuses
    enroll1 = Enrollment.objects.create(
        student=student1,
        group=group1,
        status=Enrollment.Status.ACTIVE,
        start_date=date.today() - timedelta(days=25),
        paid_from=date.today() - timedelta(days=25),
    )
    enroll2 = Enrollment.objects.create(
        student=student2,
        group=group1,
        status=Enrollment.Status.TRIAL,
        start_date=date.today() - timedelta(days=5),
    )
    enroll3 = Enrollment.objects.create(
        student=student3,
        group=group2,
        status=Enrollment.Status.FROZEN,
        start_date=date.today() - timedelta(days=15),
        paid_from=date.today() - timedelta(days=15),
        frozen_from=date.today() - timedelta(days=3),
    )

    # Create finished enrollment (left this month)
    current_month_start = date.today().replace(day=1)
    # Set end_date to middle of current month to avoid edge cases
    today = date.today()
    # If today is 1st-5th of month, set end_date to day 10, else use today
    if today.day <= 5:
        end_date_this_month = today.replace(day=10)
    else:
        end_date_this_month = today

    Enrollment.objects.create(
        student=student4,
        group=group1,
        status=Enrollment.Status.FINISHED,
        start_date=today - timedelta(days=60),
        end_date=end_date_this_month,  # Guaranteed to be in current month
    )

    # Create finished enrollment from LAST month (should NOT count)
    last_month = (current_month_start - timedelta(days=1))
    student5 = Student.objects.create(
        first_name="Student",
        last_name="Five",
        phone="+998905555555",
        status=Student.Status.ARCHIVED,
    )
    Enrollment.objects.create(
        student=student5,
        group=group1,
        status=Enrollment.Status.FINISHED,
        start_date=last_month - timedelta(days=60),
        end_date=last_month,  # Ended last month
    )

    # Create charge for student1 (make them a debtor)
    LedgerEntry.objects.create(
        student=student1,
        group=group1,
        enrollment=enroll1,
        kind=LedgerEntry.Kind.CHARGE,
        amount=-300000,  # Negative charge
        entry_date=date.today() - timedelta(days=20),
        created_by=admin,
    )

    # Create payment for student1 (but not enough)
    LedgerEntry.objects.create(
        student=student1,
        group=group1,
        enrollment=enroll1,
        kind=LedgerEntry.Kind.PAYMENT,
        amount=100000,  # Only partial payment
        method=LedgerEntry.PaymentMethod.CASH,
        entry_date=date.today() - timedelta(days=15),
        created_by=admin,
    )

    # Create payment this month for monthly income
    LedgerEntry.objects.create(
        student=student2,
        group=group1,
        enrollment=enroll2,
        kind=LedgerEntry.Kind.PAYMENT,
        amount=500000,
        method=LedgerEntry.PaymentMethod.CASH,
        entry_date=date.today(),
        created_by=admin,
    )

    # Create lessons for today
    today = date.today()
    Lesson.objects.create(
        group=group1,
        date=today,
        start_time=time(10, 0),
        end_time=time(12, 0),
        room=room1,
        teacher=teacher,
        status=Lesson.Status.SCHEDULED,
    )
    Lesson.objects.create(
        group=group2,
        date=today,
        start_time=time(14, 0),
        end_time=time(16, 0),
        room=room2,
        teacher=teacher,
        status=Lesson.Status.SCHEDULED,
    )
    # Cancelled lesson should not count
    Lesson.objects.create(
        group=group1,
        date=today,
        start_time=time(18, 0),
        end_time=time(20, 0),
        room=room1,
        teacher=teacher,
        status=Lesson.Status.CANCELLED,
    )

    # Create schedule slots for grid
    weekday = today.weekday()
    ScheduleSlot.objects.create(
        group=group1,
        weekday=weekday,
        start_time=time(10, 0),
        end_time=time(12, 0),
        room=room1,
        is_active=True,
    )
    ScheduleSlot.objects.create(
        group=group2,
        weekday=weekday,
        start_time=time(14, 0),
        end_time=time(16, 0),
        room=room2,
        is_active=True,
    )

    return {
        "director": director,
        "admin": admin,
        "teacher_user": teacher_user,
        "teacher": teacher,
        "course": course,
        "group1": group1,
        "group2": group2,
        "students": [student1, student2, student3, student4],
        "enrollments": [enroll1, enroll2, enroll3],
        "rooms": [room1, room2],
    }


@pytest.mark.django_db
class TestDashboardMetrics:
    """Test dashboard metrics calculations."""

    def test_admin_sees_all_metrics(self, client, dashboard_data):
        """Admin sees operational metrics; finance metrics are hidden (director only)."""
        client.force_login(dashboard_data["admin"])
        response = client.get(reverse("accounts:home"))

        assert response.status_code == 200
        context = response.context

        # Check that metrics are present
        assert "metrics" in context
        metrics = context["metrics"]

        # Verify metric values
        assert metrics["active_groups"] == 2  # 2 active groups
        assert metrics["active_students"] == 3  # 3 active students with enrollments
        assert metrics["total_enrollments"] == 3  # 3 non-finished enrollments
        # Moliya ko'rsatkichlari faqat direktor uchun: administrator uchun None
        assert metrics["debtors_count"] is None
        assert metrics["total_debt"] is None
        assert metrics["trial_count"] == 1  # 1 trial enrollment
        assert metrics["frozen_count"] == 1  # 1 student with status "Muzlatilgan"
        assert metrics["left_students"] == 1  # Exactly 1 finished THIS month (not last month)
        assert metrics["new_enrollments"] >= 0  # New enrollments this month
        assert metrics["active_teachers"] == 1  # 1 active teacher
        assert metrics["today_lessons"] == 2  # 2 lessons today (not cancelled)
        assert metrics["monthly_income"] is None  # finance: director only

    def test_director_sees_all_metrics(self, client, dashboard_data):
        """Director should see all metrics like admin."""
        client.force_login(dashboard_data["director"])
        response = client.get(reverse("accounts:home"))

        assert response.status_code == 200
        assert "metrics" in response.context
        assert "room_occupancy" in response.context

    def test_teacher_no_financial_metrics(self, client, dashboard_data):
        """Teacher should not see financial metrics in response."""
        client.force_login(dashboard_data["teacher_user"])
        response = client.get(reverse("accounts:home"))

        assert response.status_code == 200
        context = response.context

        # Teacher should not have metrics
        assert "metrics" not in context
        assert "room_occupancy" not in context

        # Teacher should see their lessons
        assert "today_lessons" in context
        assert context["is_teacher"] is True

        # Convert response content to string and check for absence of financial data
        content = response.content.decode("utf-8")
        assert "Qarzdorlar" not in content
        assert "Qolgan qarzlar" not in content
        assert "Oylik tushum" not in content

    def test_teacher_sees_only_their_lessons(self, client, dashboard_data):
        """Teacher should see only their own today's lessons."""
        client.force_login(dashboard_data["teacher_user"])
        response = client.get(reverse("accounts:home"))

        assert response.status_code == 200
        lessons = response.context["today_lessons"]

        # Should have 2 lessons (cancelled one excluded)
        assert len(lessons) == 2
        for lesson in lessons:
            assert lesson.teacher == dashboard_data["teacher"]


@pytest.mark.django_db
class TestDashboardScheduleGrid:
    """Test schedule grid functionality."""

    def test_schedule_grid_renders_lessons(self, client, dashboard_data):
        """Schedule grid should show lessons in correct slots."""
        client.force_login(dashboard_data["admin"])
        today_weekday = date.today().weekday()

        response = client.get(
            reverse("accounts:dashboard_schedule_grid"),
            {"weekday": today_weekday, "step": 30},
        )

        assert response.status_code == 200
        content = response.content.decode("utf-8")

        # Check that groups appear in the grid
        assert "Group A" in content
        assert "Group B" in content

        # Check that rooms appear
        assert "Room 1" in content
        assert "Room 2" in content

    def test_schedule_grid_teacher_filter(self, client, dashboard_data):
        """Teacher should see only their groups in schedule grid."""
        client.force_login(dashboard_data["teacher_user"])
        today_weekday = date.today().weekday()

        response = client.get(
            reverse("accounts:dashboard_schedule_grid"),
            {"weekday": today_weekday, "step": 30},
        )

        assert response.status_code == 200
        context = response.context
        grid = context["schedule_grid"]

        # All slots should belong to teacher's groups
        for row in grid["grid_matrix"]:
            for cell in row["cells"]:
                if cell:
                    assert cell["slot"].group.teacher == dashboard_data["teacher"]

    def test_schedule_grid_different_steps(self, client, dashboard_data):
        """Schedule grid should work with different time steps."""
        client.force_login(dashboard_data["admin"])
        today_weekday = date.today().weekday()

        for step in [15, 30, 60]:
            response = client.get(
                reverse("accounts:dashboard_schedule_grid"),
                {"weekday": today_weekday, "step": step},
            )
            assert response.status_code == 200
            grid = response.context["schedule_grid"]
            assert grid["step_minutes"] == step

    def test_schedule_grid_conflict_detection(self, db, client, dashboard_data):
        """Schedule grid should detect and mark room conflicts."""
        # Create conflicting slot (same room, overlapping time)
        today = date.today()
        weekday = today.weekday()

        # Create another teacher
        teacher2_user = User.objects.create_user(
            username="teacher2", password="test123", role=User.Role.TEACHER
        )
        teacher2 = Teacher.objects.create(
            user=teacher2_user,
            full_name="Teacher Two",
            phone="+998909999999",
            pay_type=Teacher.PayType.PERCENT,
        )

        # Create conflicting group in same room and overlapping time
        group3 = Group.objects.create(
            name="Group C",
            course=dashboard_data["course"],
            teacher=teacher2,
            status=Group.Status.ACTIVE,
            teacher_percent=30,
            start_date=date.today() - timedelta(days=10),
        )

        ScheduleSlot.objects.create(
            group=group3,
            weekday=weekday,
            start_time=time(11, 0),  # Overlaps with Group A (10:00-12:00)
            end_time=time(13, 0),
            room=dashboard_data["rooms"][0],  # Same room as Group A
            is_active=True,
        )

        client.force_login(dashboard_data["admin"])
        response = client.get(
            reverse("accounts:dashboard_schedule_grid"),
            {"weekday": weekday, "step": 30},
        )

        assert response.status_code == 200
        content = response.content.decode("utf-8")

        # Check for conflict indicator (red background)
        assert "is-conflict" in content and "Vaqt to'qnashuvi" in content


@pytest.mark.django_db
class TestDashboardQueryOptimization:
    """Test that dashboard doesn't have N+1 query problems."""

    def test_admin_dashboard_query_count(self, client, dashboard_data, django_assert_num_queries, settings):
        settings.SESSION_SAVE_EVERY_REQUEST = False  # сессию (продление таймаута) считаем отдельно: здесь только запросы приложения
        """Admin dashboard should have reasonable number of queries."""
        client.force_login(dashboard_data["admin"])
        center_name()  # markaz nomi keshda (ishchi holat): sahifa uchun alohida so'rov bo'lmaydi

        # Admin: 18 queries (session, user, operational metrics, occupancy, schedule grid,
        # room timeline seat counts, recent students + their open groups).
        # Finance metrics (debtors, monthly income) are skipped for administrator.
        # This is optimized - no N+1 problems
        with django_assert_num_queries(num=18 + TENANCY_QUERIES_PER_REQUEST, using="default"):
            response = client.get(reverse("accounts:home"))
            assert response.status_code == 200

    def test_teacher_dashboard_query_count(self, client, dashboard_data, django_assert_num_queries, settings):
        settings.SESSION_SAVE_EVERY_REQUEST = False  # сессию (продление таймаута) считаем отдельно: здесь только запросы приложения
        """Teacher dashboard should have fewer queries."""
        client.force_login(dashboard_data["teacher_user"])
        center_name()  # markaz nomi keshda (ishchi holat)

        # Teacher: 7 queries (session, user, teacher profile, lessons, schedule grid, room timeline seat counts)
        # Much fewer than admin - no financial data
        with django_assert_num_queries(num=7 + TENANCY_QUERIES_PER_REQUEST, using="default"):
            response = client.get(reverse("accounts:home"))
            assert response.status_code == 200
