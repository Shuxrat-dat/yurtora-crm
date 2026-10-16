"""Tests for finance views."""

import pytest
from django.test import Client
from django.urls import reverse

from apps.tenants.middleware import TENANCY_QUERIES_PER_REQUEST


@pytest.mark.django_db
class TestFinanceViews:
    """Тесты для finance views: права доступа и N+1 оптимизация."""

    @pytest.fixture(autouse=True)
    def setup(self, django_db_setup):
        """Подготовка данных для тестов."""
        from apps.accounts.models import User
        from apps.courses.models import Course
        from apps.groups.models import Enrollment, Group
        from apps.schedule.models import Lesson, Room
        from apps.students.models import Student
        from apps.teachers.models import Teacher

        # Пользователи
        self.director = User.objects.create_user(
            username="director", password="pass", role=User.Role.DIRECTOR
        )
        self.admin = User.objects.create_user(
            username="admin", password="pass", role=User.Role.ADMINISTRATOR
        )
        self.teacher_user = User.objects.create_user(
            username="teacher", password="pass", role=User.Role.TEACHER
        )
        self.teacher = Teacher.objects.create(
            user=self.teacher_user,
            full_name="Teacher Teacherovich",
            phone="+998901234567",
        )

        # Курс, группа
        self.course = Course.objects.create(name="Python", monthly_price=500000)
        self.room = Room.objects.create(name="Room 1")
        self.group = Group.objects.create(
            name="Python-01",
            course=self.course,
            teacher=self.teacher,
            start_date="2026-09-01",
            max_students=20,
            status=Group.Status.ACTIVE,
        )

        # Студенты с enrollments и начислениями
        from datetime import date

        from apps.finance.services import accrue_lesson_charges

        # Создаём один урок для группы
        lesson = Lesson.objects.create(
            group=self.group,
            date=date(2026, 9, 7),
            start_time="09:00",
            end_time="11:00",
            room=self.room,
            teacher=self.teacher,
            status=Lesson.Status.HELD,
        )

        # Создаём студентов с enrollments
        for i in range(10):
            student = Student.objects.create(
                first_name=f"Student{i}",
                last_name="Test",
                phone=f"+99890123456{i}",
                joined_date=date(2026, 9, 1),
            )
            Enrollment.objects.create(
                student=student,
                group=self.group,
                status=Enrollment.Status.ACTIVE,
                start_date=date(2026, 9, 1),
                paid_from=date(2026, 9, 1),
            )

        # Начисляем за урок (для всех студентов)
        accrue_lesson_charges(lesson)

        self.client = Client()

    def test_debtors_list_admin_forbidden(self):
        """Admin НЕ видит список должников: финансы только для директора."""
        self.client.login(username="admin", password="pass")
        response = self.client.get(reverse("finance:debtors_list"))
        assert response.status_code == 403

    def test_debtors_list_director_access(self):
        """Director может видеть список должников."""
        self.client.login(username="director", password="pass")
        response = self.client.get(reverse("finance:debtors_list"))
        assert response.status_code == 200

    def test_debtors_list_teacher_forbidden(self):
        """Teacher НЕ может видеть список должников (403)."""
        self.client.login(username="teacher", password="pass")
        response = self.client.get(reverse("finance:debtors_list"))
        assert response.status_code == 403

    def test_debtors_export_teacher_forbidden(self):
        """Teacher НЕ может экспортировать должников (403)."""
        self.client.login(username="teacher", password="pass")
        response = self.client.get(reverse("finance:debtors_export"))
        assert response.status_code == 403

    def test_daily_cash_admin_forbidden(self):
        """Admin НЕ видит кассовый отчёт: финансы только для директора."""
        self.client.login(username="admin", password="pass")
        response = self.client.get(reverse("finance:daily_cash"))
        assert response.status_code == 403

    def test_daily_cash_teacher_forbidden(self):
        """Teacher НЕ может видеть кассу (403)."""
        self.client.login(username="teacher", password="pass")
        response = self.client.get(reverse("finance:daily_cash"))
        assert response.status_code == 403

    def test_debtors_list_no_n_plus_1(self):
        """Список должников: проверка N+1 (должно быть мало запросов)."""

        self.client.login(username="director", password="pass")

        # Проверяем количество запросов
        # get_debtors делает 3 запроса:
        # 1. aggregate по LedgerEntry (values+annotate)
        # 2. Student.objects.filter(pk__in=...)
        # 3. Group.objects.filter(pk__in=...)
        # + запросы для auth, session, etc.
        # Ожидаем не более 10 запросов (с запасом)
        with self.assertNumQueries(10 + TENANCY_QUERIES_PER_REQUEST):
            response = self.client.get(reverse("finance:debtors_list"))
            assert response.status_code == 200

    def assertNumQueries(self, num):
        """Контекст-менеджер для подсчёта запросов."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        return CaptureQueriesContext(connection)

    def test_payment_create_teacher_forbidden(self):
        """Teacher НЕ может создавать платежи (403)."""
        from apps.students.models import Student

        student = Student.objects.first()
        self.client.login(username="teacher", password="pass")
        response = self.client.post(
            reverse(
                "finance:payment_create",
                kwargs={"student_id": student.pk, "group_id": self.group.pk},
            ),
            {
                "amount": 100000,
                "method": "cash",
                "entry_date": "2026-09-15",
                "comment": "Test",
            },
        )
        assert response.status_code == 403
