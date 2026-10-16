"""Smoke tests for list filters."""

import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.courses.models import Course
from apps.groups.models import Group
from apps.students.models import Student
from apps.teachers.models import Teacher


@pytest.mark.django_db
class TestStudentFilters:
    """Smoke tests for student list filters."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.admin = User.objects.create_user(
            username="admin", password="pass", role=User.Role.ADMINISTRATOR
        )
        self.student1 = Student.objects.create(
            first_name="Akmal",
            last_name="Yusupov",
            phone="+998901111111",
            status=Student.Status.ACTIVE,
            source=Student.Source.INSTAGRAM,
            school="Maktab №1",
        )
        self.student2 = Student.objects.create(
            first_name="Madina",
            last_name="Karimova",
            phone="+998902222222",
            status=Student.Status.ARCHIVED,
            is_blacklisted=True,
        )

    def test_status_filter_narrows_results(self, client):
        """Status filter reduces queryset."""
        client.login(username="admin", password="pass")

        # All students
        response = client.get(reverse("students:student_list"))
        assert response.status_code == 200
        assert len(response.context["students"]) == 2

        # Active only
        response = client.get(reverse("students:student_list"), {"status": "active"})
        assert response.status_code == 200
        assert len(response.context["students"]) == 1
        assert response.context["students"][0] == self.student1

    def test_search_filter_works(self, client):
        """Search by name works."""
        client.login(username="admin", password="pass")

        response = client.get(reverse("students:student_list"), {"q": "Akmal"})
        assert response.status_code == 200
        assert len(response.context["students"]) == 1
        assert response.context["students"][0] == self.student1

    def test_blacklist_filter_works(self, client):
        """Blacklist filter works."""
        client.login(username="admin", password="pass")

        response = client.get(reverse("students:student_list"), {"blacklist": "yes"})
        assert response.status_code == 200
        assert len(response.context["students"]) == 1
        assert response.context["students"][0] == self.student2


@pytest.mark.django_db
class TestGroupFilters:
    """Smoke tests for group list filters."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.admin = User.objects.create_user(
            username="admin", password="pass", role=User.Role.ADMINISTRATOR
        )
        course = Course.objects.create(name="English", monthly_price=400000)
        teacher_user = User.objects.create_user(
            username="teacher", password="pass", role=User.Role.TEACHER
        )
        teacher = Teacher.objects.create(
            user=teacher_user,
            full_name="John Doe",
            phone="+998901234567",
        )
        self.group1 = Group.objects.create(
            name="Beginner A1",
            course=course,
            teacher=teacher,
            status=Group.Status.ACTIVE,
            start_date="2024-01-01",
        )
        self.group2 = Group.objects.create(
            name="Advanced C1",
            course=course,
            teacher=teacher,
            status=Group.Status.CLOSED,
            start_date="2023-01-01",
        )

    def test_status_filter_works(self, client):
        """Group status filter works."""
        client.login(username="admin", password="pass")

        response = client.get(reverse("groups:group_list"), {"status": "active"})
        assert response.status_code == 200
        assert len(response.context["groups"]) == 1
        assert response.context["groups"][0] == self.group1

    def test_search_filter_works(self, client):
        """Group search works."""
        client.login(username="admin", password="pass")

        response = client.get(reverse("groups:group_list"), {"q": "Beginner"})
        assert response.status_code == 200
        assert len(response.context["groups"]) == 1
        assert response.context["groups"][0] == self.group1


@pytest.mark.django_db
class TestTeacherFilters:
    """Smoke tests for teacher list filters."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.admin = User.objects.create_user(
            username="admin", password="pass", role=User.Role.ADMINISTRATOR
        )
        self.teacher1 = Teacher.objects.create(
            full_name="John Doe",
            phone="+998901234567",
            is_active=True,
        )
        self.teacher2 = Teacher.objects.create(
            full_name="Jane Smith",
            phone="+998902345678",
            is_active=False,
        )

    def test_is_active_filter_works(self, client):
        """Teacher is_active filter works."""
        client.login(username="admin", password="pass")

        response = client.get(reverse("teachers:teacher_list"), {"is_active": "true"})
        assert response.status_code == 200
        assert len(response.context["teachers"]) == 1
        assert response.context["teachers"][0] == self.teacher1


@pytest.mark.django_db
class TestCourseFilters:
    """Smoke tests for course list filters."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.admin = User.objects.create_user(
            username="admin", password="pass", role=User.Role.ADMINISTRATOR
        )
        self.course1 = Course.objects.create(
            name="English", monthly_price=400000, is_active=True
        )
        self.course2 = Course.objects.create(
            name="Math", monthly_price=350000, is_active=False
        )

    def test_is_active_filter_works(self, client):
        """Course is_active filter works."""
        client.login(username="admin", password="pass")

        response = client.get(reverse("courses:course_list"), {"is_active": "true"})
        assert response.status_code == 200
        assert len(response.context["courses"]) == 1
        assert response.context["courses"][0] == self.course1


@pytest.mark.django_db
class TestDebtorsFilters:
    """Smoke tests for debtors list filters."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.admin = User.objects.create_user(
            username="admin", password="pass", role=User.Role.ADMINISTRATOR
        )

    def test_debtors_list_loads(self, client):
        """Debtors list loads without errors (finance: director only)."""
        User.objects.create_user(username="director", password="pass", role=User.Role.DIRECTOR)
        client.login(username="director", password="pass")

        response = client.get(reverse("finance:debtors_list"))
        assert response.status_code == 200
        assert "debtors" in response.context


@pytest.mark.django_db
class TestDailyCashFilters:
    """Smoke tests for daily cash filters."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.admin = User.objects.create_user(
            username="admin", password="pass", role=User.Role.ADMINISTRATOR
        )

    def test_daily_cash_loads_with_date_filter(self, client):
        """Daily cash report loads with date filters (finance: director only)."""
        User.objects.create_user(username="director", password="pass", role=User.Role.DIRECTOR)
        client.login(username="director", password="pass")

        response = client.get(
            reverse("finance:daily_cash"),
            {"date_from": "2024-01-01", "date_to": "2024-01-31"},
        )
        assert response.status_code == 200
        assert "payments" in response.context
        assert "method_totals" in response.context
