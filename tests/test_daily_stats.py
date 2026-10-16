"""Tests: daily statistics window (service, view, permissions, auto-open, Uzbek labels)."""

import re
from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.attendance.models import Attendance
from apps.core.daily_stats import (
    PERIOD_DATE,
    PERIOD_TODAY,
    PERIOD_YESTERDAY,
    get_daily_stats,
    resolve_period,
)
from apps.courses.models import Course
from apps.finance.models import LedgerEntry
from apps.groups.models import Enrollment, Group
from apps.schedule.models import Lesson, Room
from apps.students.models import Student
from apps.teachers.models import Teacher
from apps.tenants.middleware import TENANCY_QUERIES_PER_REQUEST

DAY = date(2026, 3, 10)
OLD = date(2026, 1, 1)


def _student(first, last, joined=OLD):
    return Student.objects.create(
        first_name=first, last_name=last, phone="+998900000000", joined_date=joined
    )


def _enroll(student, group):
    return Enrollment.objects.create(
        student=student,
        group=group,
        status=Enrollment.Status.ACTIVE,
        start_date=OLD,
        paid_from=OLD,
    )


def _lesson(group, teacher, room, start, end, status):
    return Lesson.objects.create(
        group=group,
        date=DAY,
        start_time=start,
        end_time=end,
        room=room,
        teacher=teacher,
        status=status,
    )


def _payment(student, group, amount, method, day=DAY):
    return LedgerEntry.objects.create(
        student=student,
        group=group,
        kind=LedgerEntry.Kind.PAYMENT,
        amount=amount,
        method=method,
        entry_date=day,
    )


@pytest.fixture
def world(db, teacher_user):
    other_user = User.objects.create_user(
        username="teacher_two", password="x", role=User.Role.TEACHER
    )
    teacher1 = Teacher.objects.create(user=teacher_user, full_name="T One", phone="+998901")
    teacher2 = Teacher.objects.create(user=other_user, full_name="T Two", phone="+998902")
    course = Course.objects.create(name="English", level="A1", monthly_price=400000)
    group1 = Group.objects.create(name="Alpha", course=course, teacher=teacher1, start_date=OLD)
    group2 = Group.objects.create(name="Beta", course=course, teacher=teacher2, start_date=OLD)
    room = Room.objects.create(name="R1")

    s1, s2, s3 = _student("Ali", "Aliyev"), _student("Vali", "Valiyev"), _student("Sami", "Samiyev")
    s4 = _student("Bobur", "Boburov")
    for s in (s1, s2, s3):
        _enroll(s, group1)
    _enroll(s4, group2)
    new_student = _student("Yangi", "Talaba", joined=DAY)

    held1 = _lesson(group1, teacher1, room, "10:00", "11:30", Lesson.Status.HELD)
    scheduled = _lesson(group2, teacher2, room, "12:00", "13:30", Lesson.Status.SCHEDULED)
    cancelled = _lesson(group1, teacher1, room, "14:00", "15:30", Lesson.Status.CANCELLED)
    held2 = _lesson(group2, teacher2, room, "16:00", "17:30", Lesson.Status.HELD)

    Attendance.objects.create(lesson=held1, student=s1, status=Attendance.Status.ABSENT)
    Attendance.objects.create(lesson=held1, student=s2, status=Attendance.Status.PRESENT)
    # s3: davomat belgilanmagan
    Attendance.objects.create(lesson=held2, student=s4, status=Attendance.Status.EXCUSED)

    _payment(s1, group1, 100000, LedgerEntry.PaymentMethod.CASH)
    _payment(s2, group1, 25000, LedgerEntry.PaymentMethod.CASH)
    _payment(s3, group1, 50000, LedgerEntry.PaymentMethod.CLICK)
    _payment(s4, group2, 10000, LedgerEntry.PaymentMethod.PAYME)
    _payment(s1, group1, 999, LedgerEntry.PaymentMethod.CASH, day=DAY - timedelta(days=1))
    LedgerEntry.objects.create(  # to'lov emas: hisobga olinmaydi
        student=s1, group=group1, kind=LedgerEntry.Kind.CHARGE, amount=-400000, entry_date=DAY
    )
    return {
        "teacher1": teacher1,
        "group1": group1,
        "group2": group2,
        "room": room,
        "held1": held1,
        "students": (s1, s2, s3, s4),
        "new_student": new_student,
        "scheduled": scheduled,
        "cancelled": cancelled,
    }


@pytest.mark.django_db
class TestPeriod:
    TODAY = date(2026, 10, 9)

    def test_yesterday_default_and_invalid_fallback(self):
        assert resolve_period("yesterday", today=self.TODAY) == (
            PERIOD_YESTERDAY, date(2026, 10, 8),
        )
        assert resolve_period("nonsense", today=self.TODAY)[1] == date(2026, 10, 8)
        assert resolve_period(PERIOD_DATE, "bad-date", today=self.TODAY)[1] == date(2026, 10, 8)

    def test_today_and_chosen_date(self):
        assert resolve_period("today", today=self.TODAY) == (PERIOD_TODAY, self.TODAY)
        assert resolve_period(PERIOD_DATE, "2026-03-10", today=self.TODAY) == (
            PERIOD_DATE, date(2026, 3, 10),
        )

    def test_future_date_is_clamped_to_today(self):
        assert resolve_period(PERIOD_DATE, "2030-01-01", today=self.TODAY)[1] == self.TODAY

    def test_uses_local_timezone_today(self):
        assert resolve_period("today")[1] == timezone.localdate()


@pytest.mark.django_db
class TestDailyStatsService:
    def test_director_sees_finance_totals_and_methods(self, director_user, world):
        stats = get_daily_stats(director_user, DAY)
        finance = stats["finance"]
        assert finance["total"] == 185000  # 100000 + 25000 + 50000 + 10000, boshqa kun va charge yo'q
        amounts = {m["label"]: m["amount"] for m in finance["methods"]}
        assert amounts == {
            "Naqd pul": 125000,
            "Click orqali": 50000,
            "Payme orqali": 10000,
        }
        assert sum(amounts.values()) == finance["total"]  # bitta tranzaksiya ikki marta emas

    def test_cash_and_click_always_listed_even_when_zero(self, director_user, world):
        stats = get_daily_stats(director_user, DAY + timedelta(days=5))
        labels = [m["label"] for m in stats["finance"]["methods"]]
        assert labels == ["Naqd pul", "Click orqali"]
        assert stats["finance"]["total"] == 0

    def test_attendance_counts_unique_students_and_ignores_unheld_lessons(self, director_user, world):
        att = get_daily_stats(director_user, DAY)["attendance"]
        assert att["absent_total"] == 2      # s1 (absent) + s4 (excused)
        assert att["unmarked_total"] == 1    # faqat s3; o'tkazilmagan va bekor qilingan darslar hisoblanmaydi
        groups = [(r["group"], r["absent"], r["unmarked"]) for r in att["rows"]]
        assert groups == [("Alpha", 1, 1), ("Beta", 1, 0)]  # 10:00 va 16:00; 12:00 (o'tilmagan) yo'q

    def test_same_student_absent_twice_counts_once(self, director_user, world):
        s1 = world["students"][0]
        extra = _lesson(world["group1"], world["teacher1"], world["room"], "18:00", "19:30", Lesson.Status.HELD)
        Attendance.objects.create(lesson=extra, student=s1, status=Attendance.Status.ABSENT)
        att = get_daily_stats(director_user, DAY)["attendance"]
        assert att["absent_total"] == 2

    def test_unheld_lesson_with_marks_is_listed_without_unmarked(self, director_user, world):
        s4 = world["students"][3]
        Attendance.objects.create(lesson=world["scheduled"], student=s4, status=Attendance.Status.ABSENT)
        att = get_daily_stats(director_user, DAY)["attendance"]
        beta_rows = [r for r in att["rows"] if r["start_time"].hour == 12]
        assert len(beta_rows) == 1 and beta_rows[0]["unmarked"] == 0 and not beta_rows[0]["is_held"]

    def test_administrator_has_no_finance_but_sees_new_students(self, admin_user, world):
        stats = get_daily_stats(admin_user, DAY)
        assert stats["finance"] is None
        assert stats["new_students"] == 1

    def test_teacher_sees_only_own_lessons_and_no_extras(self, teacher_user, world):
        stats = get_daily_stats(teacher_user, DAY)
        assert stats["finance"] is None and stats["new_students"] is None
        att = stats["attendance"]
        assert [r["group"] for r in att["rows"]] == ["Alpha"]
        assert att["absent_total"] == 1 and att["unmarked_total"] == 1

    def test_new_students_counts_joined_date_only(self, director_user, world):
        assert get_daily_stats(director_user, DAY)["new_students"] == 1
        assert get_daily_stats(director_user, DAY + timedelta(days=1))["new_students"] == 0


@pytest.mark.django_db
class TestDailyStatsView:
    url = "accounts:daily_stats"
    params = {"period": "date", "date": "2026-03-10"}

    def test_anonymous_redirected_to_login(self, client):
        response = client.get(reverse(self.url))
        assert response.status_code == 302
        assert reverse("accounts:login") in response["Location"]

    def test_director_gets_all_uzbek_sections(self, client, director_user, world):
        client.force_login(director_user)
        response = client.get(reverse(self.url), self.params)
        html = response.content.decode()
        assert response.status_code == 200
        assert response["Cache-Control"] == "private, no-store"
        for label in (
            "O‘quv markazingizning kunlik statistikasi",
            "Jami tushum",
            "To‘lov turlari",
            "Naqd pul",
            "Click orqali",
            "Kelmagan o‘quvchilar",
            "Davomat belgilanmagan",
            "Guruhlar bo‘yicha davomat",
            "Yangi o‘quvchilar",
        ):
            assert label in html
        assert re.search(r"185[\s ,.]?000", html)
        assert "Alpha" in html and "Beta" in html

    def test_administrator_view_has_no_finance_block(self, client, admin_user, world):
        client.force_login(admin_user)
        html = client.get(reverse(self.url), self.params).content.decode()
        assert "Jami tushum" not in html and "To‘lov turlari" not in html
        assert "Yangi o‘quvchilar" in html and "Kelmagan o‘quvchilar" in html

    def test_teacher_view_is_limited(self, client, teacher_user, world):
        client.force_login(teacher_user)
        html = client.get(reverse(self.url), self.params).content.decode()
        assert "Jami tushum" not in html and "Yangi o‘quvchilar" not in html
        assert "Alpha" in html and "Beta" not in html

    def test_empty_day_shows_uzbek_no_data_message(self, client, director_user, world):
        client.force_login(director_user)
        html = client.get(reverse(self.url), {"period": "date", "date": "2025-01-01"}).content.decode()
        assert "Ma’lumot mavjud emas" in html

    def test_default_period_is_yesterday(self, client, director_user, db):
        client.force_login(director_user)
        html = client.get(reverse(self.url)).content.decode()
        yesterday = (timezone.localdate() - timedelta(days=1)).strftime("%d.%m.%Y")
        assert yesterday in html

    def test_query_count_is_bounded(self, client, director_user, world, django_assert_max_num_queries):
        client.force_login(director_user)
        with django_assert_max_num_queries(12 + TENANCY_QUERIES_PER_REQUEST):
            client.get(reverse(self.url), self.params)


@pytest.mark.django_db
class TestAutoOpenAfterLogin:
    def test_opens_once_after_login_then_not_on_refresh(self, client, director_user, password):
        response = client.post(
            reverse("accounts:login"),
            {"username": director_user.username, "password": password},
            follow=True,
        )
        assert response.status_code == 200
        assert response.context["auto_open_daily_stats"] is True
        assert 'data-auto-open="1"' in response.content.decode()

        again = client.get(reverse("accounts:home"))
        assert again.context["auto_open_daily_stats"] is False
        assert 'data-auto-open="0"' in again.content.decode()

    def test_modal_and_reopen_button_available_for_every_role(
        self, client, director_user, admin_user, teacher_user
    ):
        for user in (director_user, admin_user, teacher_user):
            client.force_login(user)
            html = client.get(reverse("accounts:home")).content.decode()
            assert 'id="ds-modal"' in html
            assert "data-ds-open" in html
            assert "Davrni tanlang" in html
            for option in ("Kecha", "Bugun", "Tanlangan sana"):
                assert option in html

    def test_greeting_uses_full_name_dynamically(self, client, director_user):
        director_user.first_name, director_user.last_name = "Ali", "Valiyev"
        director_user.save()
        client.force_login(director_user)
        html = client.get(reverse("accounts:home")).content.decode()
        assert "Hurmatli Ali Valiyev" in html

    def test_greeting_falls_back_to_username(self, client, director_user):
        client.force_login(director_user)
        html = client.get(reverse("accounts:home")).content.decode()
        assert f"Hurmatli {director_user.username}" in html
