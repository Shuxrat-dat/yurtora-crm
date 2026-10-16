"""Tests: student status 'Muzlatilgan', removed frozen report, "To'lov" button and search."""

import re
from datetime import date

import pytest
from django.urls import NoReverseMatch, reverse

from apps.courses.models import Course
from apps.finance.models import LedgerEntry
from apps.groups.models import Enrollment, Group
from apps.students.models import Student
from apps.teachers.models import Teacher


@pytest.fixture
def school(db, teacher_user):
    teacher = Teacher.objects.create(
        user=teacher_user, full_name="Teacher One", phone="+998900000001"
    )
    course = Course.objects.create(name="English", level="A1", monthly_price=400000)
    group = Group.objects.create(
        name="G1", course=course, teacher=teacher, start_date=date(2026, 1, 1)
    )
    other_group = Group.objects.create(
        name="G2", course=course, teacher=teacher, start_date=date(2026, 1, 1)
    )
    student = Student.objects.create(
        first_name="Ali", last_name="Aliyev", phone="+998901234567"
    )
    Enrollment.objects.create(
        student=student,
        group=group,
        status=Enrollment.Status.ACTIVE,
        start_date=date(2026, 1, 1),
        paid_from=date(2026, 1, 1),
    )
    return {"group": group, "other_group": other_group, "student": student}


@pytest.mark.django_db
class TestStudentStatus:
    def test_three_statuses_available(self):
        assert set(Student.Status.values) == {"active", "archived", "frozen"}
        assert Student.Status.FROZEN.label == "Muzlatilgan"

    def test_administrator_can_set_frozen(self, client, admin_user, school):
        client.force_login(admin_user)
        url = reverse("students:student_status", args=[school["student"].pk])
        response = client.post(url, {"status": "frozen"})
        assert response.status_code == 302
        school["student"].refresh_from_db()
        assert school["student"].status == Student.Status.FROZEN

    def test_invalid_status_rejected(self, client, admin_user, school):
        client.force_login(admin_user)
        url = reverse("students:student_status", args=[school["student"].pk])
        client.post(url, {"status": "deleted"})
        school["student"].refresh_from_db()
        assert school["student"].status == Student.Status.ACTIVE

    def test_teacher_cannot_change_status(self, client, teacher_user, school):
        client.force_login(teacher_user)
        url = reverse("students:student_status", args=[school["student"].pk])
        assert client.post(url, {"status": "frozen"}).status_code == 403

    def test_list_filter_by_frozen_status(self, client, admin_user, school):
        Student.objects.create(
            first_name="Vali", last_name="Valiyev", phone="+998907777777",
            status=Student.Status.FROZEN,
        )
        client.force_login(admin_user)
        response = client.get(reverse("students:student_list"), {"status": "frozen"})
        names = [s.last_name for s in response.context["students"]]
        assert names == ["Valiyev"]

    def test_frozen_report_section_removed(self, client, admin_user):
        with pytest.raises(NoReverseMatch):
            reverse("students:frozen_report")
        client.force_login(admin_user)
        assert "Muzlatilganlar" not in client.get(reverse("accounts:home")).content.decode()


@pytest.mark.django_db
class TestPayButtonVisibility:
    def test_button_shown_for_admin_and_director_not_teacher(
        self, client, admin_user, director_user, teacher_user
    ):
        for user, expected in ((admin_user, True), (director_user, True), (teacher_user, False)):
            client.force_login(user)
            html = client.get(reverse("accounts:home")).content.decode()
            assert ("data-pay-open" in html) is expected


@pytest.mark.django_db
class TestPaymentSearch:
    url_name = "finance:payment_search"

    def test_empty_until_three_letters(self, client, admin_user, school):
        client.force_login(admin_user)
        for q in ("", "a", "al", "a l"):
            response = client.get(reverse(self.url_name), {"q": q})
            assert response.status_code == 200
            assert response.content.decode().strip() == ""

    def test_three_letters_find_by_surname_or_name(self, client, admin_user, school):
        client.force_login(admin_user)
        by_surname = client.get(reverse(self.url_name), {"q": "ali"}).content.decode()
        assert "Aliyev" in by_surname
        by_first_name = client.get(reverse(self.url_name), {"q": "Ali Ali"}).content.decode()
        assert "Aliyev" in by_first_name

    def test_no_match_shows_message(self, client, admin_user, school):
        client.force_login(admin_user)
        html = client.get(reverse(self.url_name), {"q": "zzz"}).content.decode()
        assert "Hech narsa topilmadi" in html

    def test_archived_students_not_listed(self, client, admin_user, school):
        school["student"].status = Student.Status.ARCHIVED
        school["student"].save()
        client.force_login(admin_user)
        html = client.get(reverse(self.url_name), {"q": "aliy"}).content.decode()
        assert "Aliyev" not in html

    def test_teacher_forbidden(self, client, teacher_user, school):
        client.force_login(teacher_user)
        assert client.get(reverse(self.url_name), {"q": "ali"}).status_code == 403


@pytest.mark.django_db
class TestPaymentModalForm:
    def test_form_lists_groups_of_student(self, client, admin_user, school):
        client.force_login(admin_user)
        url = reverse("finance:payment_modal_form", args=[school["student"].pk])
        html = client.get(url).content.decode()
        # Sahifada tasodifiy CSRF-token ham bor, shuning uchun faqat <option> matnlari tekshiriladi
        assert ">G1 (" in html
        assert ">G2 (" not in html  # o'quvchi G2 ga a'zo emas
        assert 'name="amount"' in html

    def test_student_without_group_gets_message(self, client, admin_user, db):
        student = Student.objects.create(
            first_name="Bobur", last_name="Karimov", phone="+998905550000"
        )
        client.force_login(admin_user)
        html = client.get(
            reverse("finance:payment_modal_form", args=[student.pk])
        ).content.decode()
        assert "hech bir guruhga" in html
        assert 'name="amount"' not in html


@pytest.mark.django_db
class TestPaymentModalCreate:
    def _post(self, client, school, **overrides):
        data = {
            "student_id": school["student"].pk,
            "group_id": school["group"].pk,
            "amount": 250000,
            "method": "cash",
            "entry_date": "2026-01-10",
            "comment": "yanvar",
            "next": "/students/",
        }
        data.update(overrides)
        return client.post(reverse("finance:payment_add"), data)

    def test_save_and_print_opens_receipt_with_auto_print(self, client, admin_user, school):
        client.force_login(admin_user)
        response = self._post(client, school, action="print")
        entry = LedgerEntry.objects.get(student=school["student"], kind=LedgerEntry.Kind.PAYMENT)
        assert response.status_code == 302
        assert response["Location"] == reverse("finance:receipt", args=[entry.pk]) + "?print=1&next=%2Fstudents%2F"
        page = client.get(response["Location"]).content.decode()
        assert "window.print()" in page and "addEventListener('load'" in page
        assert 'href="/students/"' in page  # "Ortga" to'lov oynasi ochilgan sahifaga qaytaradi

    def test_plain_save_does_not_auto_print(self, client, admin_user, school):
        client.force_login(admin_user)
        response = self._post(client, school, action="save")
        assert response["Location"] == "/students/"
        entry = LedgerEntry.objects.get(student=school["student"], kind=LedgerEntry.Kind.PAYMENT)
        page = client.get(reverse("finance:receipt", args=[entry.pk])).content.decode()
        assert "addEventListener('load'" not in page

    def test_failed_payment_with_print_does_not_open_receipt(self, client, admin_user, school):
        client.force_login(admin_user)
        response = self._post(client, school, action="print", group_id=school["other_group"].pk)
        assert response["Location"] == "/students/"
        assert not LedgerEntry.objects.filter(kind=LedgerEntry.Kind.PAYMENT).exists()

    def test_receipt_rejects_external_back_url(self, client, admin_user, school):
        client.force_login(admin_user)
        self._post(client, school)
        entry = LedgerEntry.objects.get(student=school["student"], kind=LedgerEntry.Kind.PAYMENT)
        page = client.get(reverse("finance:receipt", args=[entry.pk]) + "?next=https://evil.example/").content.decode()
        assert "evil.example" not in page

    def test_administrator_adds_payment(self, client, admin_user, school):
        client.force_login(admin_user)
        response = self._post(client, school)
        assert response.status_code == 302
        assert response["Location"] == "/students/"
        entry = LedgerEntry.objects.get(
            student=school["student"], kind=LedgerEntry.Kind.PAYMENT
        )
        assert entry.amount == 250000
        assert entry.group == school["group"]
        assert entry.created_by == admin_user

    def test_group_without_membership_rejected(self, client, admin_user, school):
        client.force_login(admin_user)
        self._post(client, school, group_id=school["other_group"].pk)
        assert not LedgerEntry.objects.filter(kind=LedgerEntry.Kind.PAYMENT).exists()

    def test_invalid_amount_rejected(self, client, admin_user, school):
        client.force_login(admin_user)
        self._post(client, school, amount=0)
        assert not LedgerEntry.objects.filter(kind=LedgerEntry.Kind.PAYMENT).exists()

    def test_external_next_url_ignored(self, client, admin_user, school):
        client.force_login(admin_user)
        response = self._post(client, school, next="https://evil.example/")
        assert response.status_code == 302
        assert response["Location"] == reverse(
            "students:student_detail", args=[school["student"].pk]
        )

    def test_teacher_forbidden(self, client, teacher_user, school):
        client.force_login(teacher_user)
        assert self._post(client, school).status_code == 403
        assert not LedgerEntry.objects.filter(kind=LedgerEntry.Kind.PAYMENT).exists()


@pytest.mark.django_db
class TestPayButtonDoesNotDependOnHtmx:
    """«To'lov» oynasi tashqi kutubxona (HTMX/CDN) yuklanmasa ham ishlashi kerak."""

    def test_search_input_has_no_htmx_attributes(self, client, admin_user):
        client.force_login(admin_user)
        html = client.get(reverse("accounts:home")).content.decode()
        input_tag = re.search(r'<input[^>]*id="pay-search"[^>]*>', html, re.S).group(0)
        assert f'data-url="{reverse("finance:payment_search")}"' in input_tag
        assert "hx-" not in input_tag

    def test_results_use_data_attribute_instead_of_htmx(self, client, admin_user, school):
        client.force_login(admin_user)
        html = client.get(reverse("finance:payment_search"), {"q": "ali"}).content.decode()
        form_url = reverse("finance:payment_modal_form", args=[school["student"].pk])
        assert f'data-pay-pick="{form_url}"' in html
        assert "hx-get" not in html

    def test_static_assets_are_versioned_to_bypass_stale_browser_cache(self, client, admin_user):
        client.force_login(admin_user)
        html = client.get(reverse("accounts:home")).content.decode()
        assert re.search(r"app\.css\?v=\d+", html)
        assert re.search(r"app\.js\?v=\d+", html)
