"""Audit tests for list filters: students, groups, teachers, courses, schedule, finance."""

import re
from datetime import date

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.courses.models import Course
from apps.finance.models import LedgerEntry
from apps.groups.models import Enrollment, Group
from apps.schedule.models import Lesson, Room, ScheduleSlot
from apps.students.models import Student
from apps.teachers.models import Teacher

OLD = date(2026, 1, 1)
D1, D2, D3 = date(2026, 3, 10), date(2026, 3, 11), date(2026, 3, 12)


def _student(first, last, phone, **extra):
    return Student.objects.create(
        first_name=first, last_name=last, phone=phone, joined_date=OLD, **extra
    )


def _enroll(student, group, status=Enrollment.Status.ACTIVE):
    return Enrollment.objects.create(
        student=student, group=group, status=status, start_date=OLD, paid_from=OLD
    )


@pytest.fixture
def world(db, teacher_user):
    user2 = User.objects.create_user(username="t2", password="x", role=User.Role.TEACHER)
    teacher1 = Teacher.objects.create(user=teacher_user, full_name="Tea One", phone="+998901")
    teacher2 = Teacher.objects.create(user=user2, full_name="Tea Two", phone="+998902", is_active=False)
    english = Course.objects.create(name="English", level="A1", monthly_price=400000)
    russian = Course.objects.create(name="Russian", level="A1", monthly_price=300000, is_active=False)
    group_a = Group.objects.create(name="Alpha", course=english, teacher=teacher1, start_date=OLD)
    group_b = Group.objects.create(
        name="Beta", course=russian, teacher=teacher2, start_date=OLD, status=Group.Status.CLOSED
    )
    anvar = _student("Anvar", "Karimov", "+998901234567", source=Student.Source.INSTAGRAM, school="Maktab 5")
    bobur = _student(
        "Bobur", "Toshmatov", "+998907654321", status=Student.Status.ARCHIVED,
        source=Student.Source.TELEGRAM, is_blacklisted=True,
    )
    sardor = _student("Sardor", "Rahimov", "+998935550000", status=Student.Status.FROZEN)
    _enroll(anvar, group_a)
    _enroll(bobur, group_b)
    _enroll(sardor, group_a)
    _enroll(sardor, group_b)  # sardor ikkala guruhda
    trial = _student("Temur", "Sinov", "+998940000000")
    _enroll(trial, group_a, status=Enrollment.Status.TRIAL)
    return {
        "teacher1": teacher1, "teacher2": teacher2, "group_a": group_a, "group_b": group_b,
        "english": english, "russian": russian,
        "anvar": anvar, "bobur": bobur, "sardor": sardor, "trial": trial,
    }


def _names(response, key="students"):
    return sorted(s.last_name for s in response.context[key])


@pytest.mark.django_db
class TestStudentFilters:
    url = "students:student_list"

    def get(self, client, user, **params):
        client.force_login(user)
        return client.get(reverse(self.url), params)

    def test_no_filters_lists_everyone(self, client, admin_user, world):
        assert len(self.get(client, admin_user).context["students"]) == 4

    def test_search_words_any_order_and_case(self, client, admin_user, world):
        for q in ("anvar karimov", "KARIMOV Anvar", "  karimov   ", "ANV"):
            assert _names(self.get(client, admin_user, q=q)) == ["Karimov"], q

    def test_search_requires_every_word(self, client, admin_user, world):
        assert _names(self.get(client, admin_user, q="anvar toshmatov")) == []

    def test_search_by_phone_with_spaces(self, client, admin_user, world):
        assert _names(self.get(client, admin_user, q="+998 90 123 45 67")) == ["Karimov"]

    def test_teacher_cannot_search_by_phone(self, client, teacher_user, world):
        assert _names(self.get(client, teacher_user, q="+998901234567")) == []

    def test_status_filters(self, client, admin_user, world):
        assert _names(self.get(client, admin_user, status="archived")) == ["Toshmatov"]
        assert _names(self.get(client, admin_user, status="frozen")) == ["Rahimov"]
        assert _names(self.get(client, admin_user, status="active")) == ["Karimov", "Sinov"]

    def test_trial_status_link_from_dashboard(self, client, admin_user, world):
        assert _names(self.get(client, admin_user, status="trial")) == ["Sinov"]

    def test_group_filter(self, client, admin_user, world):
        assert _names(self.get(client, admin_user, group=world["group_a"].pk)) == [
            "Karimov", "Rahimov", "Sinov",
        ]

    def test_group_and_teacher_apply_to_the_same_enrollment(self, client, admin_user, world):
        # sardor Alpha (teacher1) va Beta (teacher2) da; Alpha + teacher2 = hech kim
        response = self.get(
            client, admin_user, group=world["group_a"].pk, teacher=world["teacher2"].pk
        )
        assert _names(response) == []
        response = self.get(
            client, admin_user, group=world["group_b"].pk, teacher=world["teacher2"].pk
        )
        assert _names(response) == ["Rahimov", "Toshmatov"]

    def test_source_school_blacklist_and_combination(self, client, admin_user, world):
        assert _names(self.get(client, admin_user, source="telegram")) == ["Toshmatov"]
        assert _names(self.get(client, admin_user, school="maktab")) == ["Karimov"]
        assert _names(self.get(client, admin_user, blacklist="yes")) == ["Toshmatov"]
        assert "Toshmatov" not in _names(self.get(client, admin_user, blacklist="no"))
        combined = self.get(client, admin_user, source="instagram", status="active", school="Maktab")
        assert _names(combined) == ["Karimov"]
        assert _names(self.get(client, admin_user, source="instagram", status="archived")) == []

    def test_invalid_values_are_ignored_not_500(self, client, admin_user, world):
        response = self.get(client, admin_user, group="abc", teacher="1;drop", status="zzz", source="x")
        assert response.status_code == 200
        assert len(response.context["students"]) == 4

    def test_no_results_page_renders(self, client, admin_user, world):
        response = self.get(client, admin_user, q="nobody-here")
        assert response.status_code == 200
        assert list(response.context["students"]) == []

    def test_pagination_applies_to_filtered_set(self, client, admin_user, world):
        for i in range(20):
            _student(f"Bulk{i}", "Bulkov", f"+99890000{i:04d}")
        page1 = self.get(client, admin_user, q="bulkov")
        assert page1.context["paginator"].count == 20
        assert len(page1.context["students"]) == 15
        page2 = self.get(client, admin_user, q="bulkov", page=2)
        assert len(page2.context["students"]) == 5
        # sahifa havolalarida filtr saqlanadi
        assert "q=bulkov" in page1.content.decode()

    def test_htmx_request_returns_only_the_table(self, client, admin_user, world):
        client.force_login(admin_user)
        response = client.get(reverse(self.url), {"q": "anvar"}, HTTP_HX_REQUEST="true")
        html = response.content.decode()
        assert "students/_student_table.html" in [t.name for t in response.templates]
        assert "<html" not in html and "Karimov" in html and "Toshmatov" not in html

    def test_form_triggers_live_filtering_on_the_form_itself(self, client, admin_user, world):
        html = self.get(client, admin_user).content.decode()
        # hx-get bolalarga meros bo'lmaydi: trigger formada bo'lishi shart
        assert re.search(r'hx-trigger="submit, change from:find select', html)
        assert 'hx-trigger="change"' not in html
        assert "Qidirish" in html and "Barcha filtrlarni tozalash" in html

    def test_teacher_sees_only_own_students_even_with_foreign_group_param(self, client, teacher_user, world):
        assert _names(self.get(client, teacher_user)) == ["Karimov", "Rahimov", "Sinov"]
        foreign = self.get(client, teacher_user, group=world["group_b"].pk)
        assert _names(foreign) == []


@pytest.mark.django_db
class TestGroupTeacherCourseFilters:
    def test_groups_search_by_group_or_course_name(self, client, admin_user, world):
        client.force_login(admin_user)
        url = reverse("groups:group_list")

        def names(**params):
            return sorted(g.name for g in client.get(url, params).context["groups"])

        assert names() == ["Alpha", "Beta"]
        assert names(q="alp") == ["Alpha"]
        assert names(q="russ") == ["Beta"]               # kurs nomi bo'yicha
        assert names(q="alpha english") == ["Alpha"]     # so'zlar birgalikda
        assert names(q="alpha russian") == []
        assert names(status="closed") == ["Beta"]
        assert names(status="active", q="beta") == []
        assert names(status="bogus") == ["Alpha", "Beta"]

    def test_teacher_sees_only_own_groups_when_filtering(self, client, teacher_user, world):
        client.force_login(teacher_user)
        response = client.get(reverse("groups:group_list"), {"q": "beta"})
        assert list(response.context["groups"]) == []

    def test_teachers_is_active_filter(self, client, admin_user, world):
        client.force_login(admin_user)
        url = reverse("teachers:teacher_list")

        def names(value):
            return sorted(t.full_name for t in client.get(url, {"is_active": value}).context["teachers"])

        assert names("true") == ["Tea One"]
        assert names("false") == ["Tea Two"]
        assert names("") == ["Tea One", "Tea Two"]
        assert names("garbage") == ["Tea One", "Tea Two"]

    def test_courses_is_active_filter(self, client, admin_user, world):
        client.force_login(admin_user)
        url = reverse("courses:course_list")

        def names(value):
            return sorted(c.name for c in client.get(url, {"is_active": value}).context["courses"])

        assert names("true") == ["English"]
        assert names("false") == ["Russian"]
        assert names("") == ["English", "Russian"]


@pytest.mark.django_db
class TestScheduleFilters:
    WEEK = "2026-03-09"

    @pytest.fixture
    def lessons(self, world):
        room1, room2 = Room.objects.create(name="R1"), Room.objects.create(name="R2")
        l1 = Lesson.objects.create(
            group=world["group_a"], date=D1, start_time="10:00", end_time="11:00",
            room=room1, teacher=world["teacher1"],
        )
        l2 = Lesson.objects.create(
            group=world["group_b"], date=D2, start_time="12:00", end_time="13:00",
            room=room2, teacher=world["teacher2"],
        )
        return {"l1": l1, "l2": l2, "room1": room1, "room2": room2}

    def count(self, client, **params):
        response = client.get(reverse("schedule:weekly_schedule"), {"week": self.WEEK, **params})
        assert response.status_code == 200
        return sum(len(v) for v in response.context["lessons_by_day"].values())

    def test_weekly_filters_single_and_combined(self, client, admin_user, world, lessons):
        client.force_login(admin_user)
        assert self.count(client) == 2
        assert self.count(client, group=world["group_a"].pk) == 1
        assert self.count(client, teacher=world["teacher2"].pk) == 1
        assert self.count(client, room=lessons["room2"].pk) == 1
        assert self.count(client, group=world["group_a"].pk, teacher=world["teacher2"].pk) == 0
        assert self.count(client, group=world["group_b"].pk, room=lessons["room2"].pk) == 1

    def test_weekly_invalid_values_do_not_crash(self, client, admin_user, world, lessons):
        client.force_login(admin_user)
        assert self.count(client, group="abc", teacher="x'", room="1e9999") == 2

    def test_week_boundaries(self, client, admin_user, world, lessons):
        client.force_login(admin_user)
        response = client.get(reverse("schedule:weekly_schedule"), {"week": "2026-03-16"})
        assert sum(len(v) for v in response.context["lessons_by_day"].values()) == 0
        response = client.get(reverse("schedule:weekly_schedule"), {"week": "not-a-date"})
        assert response.status_code == 200

    def test_teacher_filter_cannot_expose_other_teachers_lessons(self, client, teacher_user, world, lessons):
        client.force_login(teacher_user)
        assert self.count(client, teacher=world["teacher2"].pk) == 0
        assert self.count(client) == 1

    def test_slot_list_group_filter(self, client, admin_user, world, lessons):
        for group in (world["group_a"], world["group_b"]):
            ScheduleSlot.objects.create(
                group=group, weekday=0, start_time="10:00", end_time="11:00", room=lessons["room1"]
            )
        client.force_login(admin_user)
        url = reverse("schedule:slot_list")
        assert len(client.get(url).context["slots"]) == 2
        assert [s.group_id for s in client.get(url, {"group": world["group_a"].pk}).context["slots"]] == [
            world["group_a"].pk
        ]
        assert client.get(url, {"group": "abc"}).status_code == 200

    def test_home_schedule_grid_garbage_params(self, client, admin_user, world):
        client.force_login(admin_user)
        response = client.get(reverse("accounts:dashboard_schedule_grid"), {"weekday": "x", "step": "y"})
        assert response.status_code == 200


def _debt(student, group, amount):
    return LedgerEntry.objects.create(
        student=student, group=group, kind=LedgerEntry.Kind.CHARGE, amount=-amount, entry_date=D1
    )


@pytest.mark.django_db
class TestDebtorsFilters:
    url = "finance:debtors_list"

    @pytest.fixture
    def debts(self, world):
        _debt(world["anvar"], world["group_a"], 100000)
        _debt(world["bobur"], world["group_b"], 50000)
        _debt(world["sardor"], world["group_a"], 30000)

    def get(self, client, user, **params):
        client.force_login(user)
        return client.get(reverse(self.url), params)

    def test_unfiltered_total(self, client, director_user, world, debts):
        response = self.get(client, director_user)
        assert len(response.context["debtors"]) == 3
        assert response.context["total_debt"] == -180000

    def test_group_filter_recalculates_total(self, client, director_user, world, debts):
        response = self.get(client, director_user, group=world["group_a"].pk)
        assert {d["student"].last_name for d in response.context["debtors"]} == {"Karimov", "Rahimov"}
        assert response.context["total_debt"] == -130000

    def test_search_words_phone_and_combination(self, client, director_user, world, debts):
        by_name = self.get(client, director_user, q="KARIMOV anvar")
        assert [d["student"].last_name for d in by_name.context["debtors"]] == ["Karimov"]
        by_phone = self.get(client, director_user, q="+998 93 555")
        assert [d["student"].last_name for d in by_phone.context["debtors"]] == ["Rahimov"]
        combo = self.get(client, director_user, q="rahimov", group=world["group_b"].pk)
        assert list(combo.context["debtors"]) == []
        assert combo.context["total_debt"] == 0

    def test_invalid_group_does_not_crash(self, client, director_user, world, debts):
        response = self.get(client, director_user, group="abc")
        assert response.status_code == 200 and len(response.context["debtors"]) == 3

    def test_pagination_links_keep_filters_without_duplicate_page(self, client, director_user, world):
        for i in range(60):
            _debt(_student(f"Bulk{i}", "Bulkov", f"+99890111{i:04d}"), world["group_a"], 1000)
        response = self.get(client, director_user, q="bulkov", page=2)
        assert response.context["page_obj"].number == 2
        hrefs = re.findall(r'href="(\?[^"]*)"', response.content.decode())
        page_links = [h for h in hrefs if "page=" in h]
        assert page_links, "sahifa havolasi topilmadi"
        for href in page_links:
            assert href.count("page=") == 1 and "q=bulkov" in href

    def test_csv_export_respects_filters(self, client, director_user, world, debts):
        client.force_login(director_user)
        response = client.get(reverse("finance:debtors_export"), {"group": world["group_a"].pk, "q": "anvar"})
        text = response.content.decode("utf-8-sig")
        assert "Karimov" in text and "Rahimov" not in text and "Toshmatov" not in text

    def test_only_director_can_filter_debtors(self, client, admin_user, teacher_user, world, debts):
        for user in (admin_user, teacher_user):
            assert self.get(client, user, q="anvar").status_code == 403


def _payment(student, group, amount, method, day):
    return LedgerEntry.objects.create(
        student=student, group=group, kind=LedgerEntry.Kind.PAYMENT, amount=amount,
        method=method, entry_date=day,
    )


@pytest.fixture
def payments(world):
    cash, click = LedgerEntry.PaymentMethod.CASH, LedgerEntry.PaymentMethod.CLICK
    _payment(world["anvar"], world["group_a"], 100000, cash, D1)
    _payment(world["anvar"], world["group_a"], 50000, click, D2)
    _payment(world["sardor"], world["group_a"], 25000, cash, D3)


@pytest.mark.django_db
class TestDailyCashFilters:
    url = "finance:daily_cash"

    def get(self, client, user, **params):
        client.force_login(user)
        return client.get(reverse(self.url), params)

    def test_inclusive_range_and_totals(self, client, director_user, world, payments):
        response = self.get(client, director_user, date_from="2026-03-10", date_to="2026-03-11")
        assert response.context["total_amount"] == 150000
        assert len(response.context["payments"]) == 2
        single = self.get(client, director_user, date_from="2026-03-12", date_to="2026-03-12")
        assert single.context["total_amount"] == 25000

    def test_reversed_range_is_swapped(self, client, director_user, world, payments):
        response = self.get(client, director_user, date_from="2026-03-11", date_to="2026-03-10")
        assert response.context["total_amount"] == 150000
        assert response.context["current_date_from"] == "2026-03-10"

    def test_method_filter_and_totals_by_method(self, client, director_user, world, payments):
        response = self.get(client, director_user, date_from="2026-03-10", date_to="2026-03-12", method="cash")
        assert response.context["total_amount"] == 125000
        assert response.context["method_totals"]["Naqd"] == 125000
        assert response.context["method_totals"]["Click"] == 0
        unfiltered = self.get(client, director_user, date_from="2026-03-10", date_to="2026-03-12")
        assert unfiltered.context["total_amount"] == 175000
        assert unfiltered.context["method_totals"]["Click"] == 50000

    def test_single_bound_is_completed_and_garbage_falls_back_to_today(self, client, director_user, world, payments):
        from_only = self.get(client, director_user, date_from="2026-03-11")
        assert from_only.context["total_amount"] == 75000  # D2 va D3, tugash = bugun
        today = timezone.localdate()
        garbage = self.get(client, director_user, date_from="zzz", date_to="2026-99-99", method="nope")
        assert garbage.status_code == 200
        assert garbage.context["date_from"] == today and garbage.context["date_to"] == today

    def test_no_results(self, client, director_user, world, payments):
        response = self.get(client, director_user, date_from="2020-01-01", date_to="2020-01-02")
        assert response.context["total_amount"] == 0 and len(response.context["payments"]) == 0

    def test_access(self, client, admin_user, teacher_user, world, payments):
        for user in (admin_user, teacher_user):
            assert self.get(client, user, method="cash").status_code == 403


@pytest.mark.django_db
class TestFinanceDashboardAndPayrollFilters:
    def dashboard(self, client, user, **params):
        client.force_login(user)
        return client.get(reverse("finance:dashboard"), params)

    def test_month_and_year(self, client, director_user, world, payments):
        response = self.dashboard(client, director_user, year=2026, month=3)
        assert response.context["total_income"] == 175000
        assert (response.context["start_date"], response.context["end_date"]) == (
            date(2026, 3, 1), date(2026, 3, 31),
        )
        assert self.dashboard(client, director_user, year=2026, month=4).context["total_income"] == 0

    def test_date_range_overrides_month_and_method_applies(self, client, director_user, world, payments):
        ranged = self.dashboard(
            client, director_user, year=2026, month=3, date_from="2026-03-11", date_to="2026-03-12"
        )
        assert ranged.context["total_income"] == 75000
        cash = self.dashboard(client, director_user, year=2026, month=3, method="cash")
        assert cash.context["total_income"] == 125000

    def test_reversed_and_single_bound_ranges(self, client, director_user, world, payments):
        reversed_range = self.dashboard(
            client, director_user, year=2026, month=3, date_from="2026-03-12", date_to="2026-03-10"
        )
        assert reversed_range.context["total_income"] == 175000
        from_only = self.dashboard(client, director_user, year=2026, month=3, date_from="2026-03-12")
        assert from_only.context["total_income"] == 25000

    def test_garbage_parameters_do_not_crash(self, client, director_user, world):
        response = self.dashboard(
            client, director_user, year="abc", month="99", date_from="x", date_to="y", method="zzz"
        )
        assert response.status_code == 200
        assert response.context["year"] == timezone.localdate().year

    def test_changing_month_clears_stale_date_range_in_form(self, client, director_user, world):
        html = self.dashboard(client, director_user).content.decode()
        assert html.count("this.form.date_from.value=''") == 2  # yil va oy tanlagichlari

    def test_payroll_and_my_salary_garbage_period(self, client, director_user, world):
        client.force_login(director_user)
        for params in ({"year": "abc", "month": "13"}, {"year": "1", "month": "0"}):
            assert client.get(reverse("finance:payroll"), params).status_code == 200
            assert client.get(reverse("finance:payroll_export"), params).status_code == 200
            response = client.get(
                reverse("finance:my_salary"), {**params, "teacher_id": world["teacher1"].pk}
            )
            assert response.status_code == 200

    def test_finance_filters_stay_director_only(self, client, admin_user, teacher_user, world):
        for user in (admin_user, teacher_user):
            client.force_login(user)
            assert client.get(reverse("finance:dashboard"), {"year": 2026}).status_code == 403
            assert client.get(reverse("finance:payroll"), {"year": 2026}).status_code == 403
