"""Tests for payroll functionality."""

from datetime import date

import pytest
from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.accounts.models import User
from apps.courses.models import Course
from apps.finance import services
from apps.finance.models import LedgerEntry
from apps.groups.models import Enrollment, Group
from apps.schedule.models import Lesson, Room
from apps.students.models import Student
from apps.teachers.models import Teacher


@pytest.mark.django_db
class PayrollTestCase(TestCase):
    """Тесты для расчёта зарплаты."""

    def setUp(self):
        """Подготовка данных."""
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
            full_name="Teacher Test",
            phone="+998901234567",
            pay_type=Teacher.PayType.PERCENT,
        )
        self.course = Course.objects.create(name="Python", monthly_price=500000)
        self.room = Room.objects.create(name="Room 1")
        self.group = Group.objects.create(
            name="Python-01",
            course=self.course,
            teacher=self.teacher,
            start_date=date(2026, 8, 1),
            max_students=20,
            teacher_percent=30,
            status=Group.Status.ACTIVE,
        )
        self.student = Student.objects.create(
            first_name="Student",
            last_name="Test",
            phone="+998901111111",
            joined_date=date(2026, 8, 1),
        )

    def test_percent_calculated_from_payments_not_charges(self):
        """Процент считается от оплат (payment), а не от начислений (charge)."""
        Enrollment.objects.create(
            student=self.student,
            group=self.group,
            status=Enrollment.Status.ACTIVE,
            start_date=date(2026, 8, 1),
            paid_from=date(2026, 8, 1),
        )

        # Начисляем за урок (charge: -35000)
        lesson = Lesson.objects.create(
            group=self.group,
            date=date(2026, 8, 5),
            start_time="09:00",
            end_time="11:00",
            room=self.room,
            teacher=self.teacher,
            status=Lesson.Status.HELD,
        )
        services.accrue_lesson_charges(lesson)

        # Оплата 100000
        services.register_payment(
            student=self.student,
            group=self.group,
            amount=100000,
            method=LedgerEntry.PaymentMethod.CASH,
            entry_date=date(2026, 8, 10),
            comment="Test payment",
            user=self.admin,
        )

        # Процент считается от 100000, а не от 35000
        payroll = services.compute_payroll(2026, 8)
        teacher_data = payroll[self.teacher.pk]

        self.assertEqual(len(teacher_data["groups"]), 1)
        self.assertEqual(teacher_data["groups"][0]["base"], 100000)
        self.assertEqual(teacher_data["groups"][0]["percent"], 30)
        self.assertEqual(teacher_data["groups"][0]["amount"], 30000)  # 100000 * 30 // 100

    def test_payment_month_boundary(self):
        """Платёж на границе месяцев попадает в свой месяц."""
        services.register_payment(
            student=self.student,
            group=self.group,
            amount=200000,
            method=LedgerEntry.PaymentMethod.CASH,
            entry_date=date(2026, 9, 30),  # September
            comment="Sep payment",
            user=self.admin,
        )
        services.register_payment(
            student=self.student,
            group=self.group,
            amount=300000,
            method=LedgerEntry.PaymentMethod.CASH,
            entry_date=date(2026, 8, 1),  # October
            comment="Oct payment",
            user=self.admin,
        )

        # September payroll
        payroll_sep = services.compute_payroll(2026, 9)
        sep_amount = payroll_sep[self.teacher.pk]["groups"][0]["amount"]
        self.assertEqual(sep_amount, 60000)  # 200000 * 30 // 100

        # October payroll
        payroll_oct = services.compute_payroll(2026, 8)
        oct_amount = payroll_oct[self.teacher.pk]["groups"][0]["amount"]
        self.assertEqual(oct_amount, 90000)  # 300000 * 30 // 100

    def test_fixed_salary_independent_of_payments(self):
        """Fixed зарплата не зависит от оплат."""
        self.teacher.pay_type = Teacher.PayType.FIXED
        self.teacher.monthly_salary = 5000000
        self.teacher.save()

        # Добавляем оплаты
        services.register_payment(
            student=self.student,
            group=self.group,
            amount=1000000,
            method=LedgerEntry.PaymentMethod.CASH,
            entry_date=date(2026, 8, 15),
            comment="Payment",
            user=self.admin,
        )

        payroll = services.compute_payroll(2026, 8)
        teacher_data = payroll[self.teacher.pk]

        self.assertEqual(teacher_data["pay_type"], Teacher.PayType.FIXED)
        self.assertEqual(teacher_data["fixed_salary"], 5000000)
        self.assertEqual(teacher_data["total_accrued"], 5000000)
        self.assertEqual(len(teacher_data["groups"]), 0)  # Нет групп для fixed

    def test_closed_month_immutable_after_percent_change(self):
        """Закрытый месяц не меняется после изменения процента."""
        # Оплата в октябре с 30%
        services.register_payment(
            student=self.student,
            group=self.group,
            amount=1000000,
            method=LedgerEntry.PaymentMethod.CASH,
            entry_date=date(2026, 8, 15),
            comment="Payment",
            user=self.admin,
        )

        # Закрываем октябрь
        services.close_payroll_month(2026, 8, self.director)

        # Меняем процент на 50%
        self.group.teacher_percent = 50
        self.group.save()

        # Октябрь остаётся с 30%
        payroll_oct = services.compute_payroll(2026, 8)
        oct_amount = payroll_oct[self.teacher.pk]["groups"][0]["amount"]
        self.assertEqual(oct_amount, 300000)  # 1000000 * 30 // 100, не 50%

    def test_payment_in_closed_month_rejected(self):
        """Платёж в закрытый месяц запрещён."""
        services.close_payroll_month(2026, 8, self.director)

        with self.assertRaises(ValidationError) as cm:
            services.register_payment(
                student=self.student,
                group=self.group,
                amount=100000,
                method=LedgerEntry.PaymentMethod.CASH,
                entry_date=date(2026, 8, 15),
                comment="Late payment",
                user=self.admin,
            )

        self.assertIn("yopilgan", str(cm.exception).lower())

    def test_reclose_rejected(self):
        """Повторное закрытие запрещено."""
        services.close_payroll_month(2026, 8, self.director)

        with self.assertRaises(ValidationError) as cm:
            services.close_payroll_month(2026, 8, self.director)

        self.assertIn("allaqachon yopilgan", str(cm.exception).lower())

    def test_immutability_of_payroll_models(self):
        """Проверка неизменяемости новых моделей."""
        from apps.finance.models import PayrollLine

        services.register_payment(
            student=self.student,
            group=self.group,
            amount=500000,
            method=LedgerEntry.PaymentMethod.CASH,
            entry_date=date(2026, 8, 15),
            comment="Payment",
            user=self.admin,
        )

        payroll_month = services.close_payroll_month(2026, 8, self.director)

        # Попытка изменить PayrollMonth
        payroll_month.year = 2027
        with self.assertRaises(ValidationError):
            payroll_month.save()

        # Попытка изменить PayrollLine
        line = PayrollLine.objects.first()
        line.amount = 999999
        with self.assertRaises(ValidationError):
            line.save()

        # Попытка удалить
        with self.assertRaises(ValidationError):
            line.delete()

    def test_teacher_cannot_access_full_payroll(self):
        """Teacher не может видеть полную ведомость (должен быть 403)."""
        from django.test import Client

        client = Client()
        client.force_login(self.teacher_user)

        response = client.get("/finance/payroll/")
        self.assertEqual(response.status_code, 403)

    def test_teacher_sees_only_own_salary(self):
        """Teacher видит только свою зарплату."""
        # Создаём второго учителя
        teacher2_user = User.objects.create_user(
            username="teacher2", password="pass", role=User.Role.TEACHER
        )
        teacher2 = Teacher.objects.create(
            user=teacher2_user,
            full_name="Teacher 2",
            phone="+998902222222",
            pay_type=Teacher.PayType.FIXED,
            monthly_salary=3000000,
        )

        services.register_payment(
            student=self.student,
            group=self.group,
            amount=1000000,
            method=LedgerEntry.PaymentMethod.CASH,
            entry_date=date(2026, 8, 15),
            comment="Payment",
            user=self.admin,
        )

        payroll = services.compute_payroll(2026, 8)

        # Оба учителя есть в данных
        self.assertIn(self.teacher.pk, payroll)
        self.assertIn(teacher2.pk, payroll)

        # Через view: учитель НЕ имеет доступа к ФОТ (финансы только для директора)
        from django.test import Client

        client = Client()
        client.force_login(self.teacher_user)
        response = client.get("/finance/my-salary/")
        self.assertEqual(response.status_code, 403)

        # Директор видит зарплату выбранного учителя, но не чужую в том же запросе
        director_client = Client()
        director_client.force_login(self.director)
        response = director_client.get(
            "/finance/my-salary/",
            {"teacher_id": self.teacher.pk, "year": 2026, "month": 8},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.teacher.full_name)
        self.assertNotContains(response, teacher2.full_name)
