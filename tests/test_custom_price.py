"""Shaxsiy narx: o'quvchi kurs narxidan farqli summa to'laydi (200 000, 300 000 ...)."""

from datetime import date, time

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from apps.courses.models import Course
from apps.finance import services
from apps.finance.models import LedgerEntry
from apps.groups.models import Enrollment, EnrollmentLog, Group
from apps.groups.services import enroll_student
from apps.schedule.models import Lesson, Room
from apps.students.models import Student
from apps.teachers.models import Teacher

pytestmark = pytest.mark.django_db

PRICE = 350000
LESSONS = 10  # oktabrda 10 ta dars: har biri 35 000 (kurs narxida)


@pytest.fixture
def center(director_user):
    teacher = Teacher.objects.create(full_name="Ustoz", phone="+998900000080")
    course = Course.objects.create(name="Matematika", monthly_price=PRICE)
    group = Group.objects.create(name="Mat-1", course=course, teacher=teacher, start_date="2026-10-01")
    room = Room.objects.create(name="1")
    lessons = [
        Lesson.objects.create(group=group, date=date(2026, 10, d), start_time=time(10), end_time=time(11), room=room, teacher=teacher)
        for d in range(1, LESSONS + 1)
    ]
    return {"group": group, "lessons": lessons, "user": director_user}


def student(name):
    return Student.objects.create(first_name=name, last_name="Talaba", phone="")


def enroll(center, st, price=None, note=""):
    return enroll_student(st, center["group"], start_date=date(2026, 10, 1), user=center["user"], custom_price=price, price_note=note)


def plain_enroll(center, st, price=None):
    """Zapis bevosita yaratiladi: o'tgan darslar avtomatik hisoblanmaydi (darslar bir-bir o'tkaziladi)."""
    return Enrollment.objects.create(
        student=st, group=center["group"], status="active", start_date=date(2026, 10, 1), paid_from=date(2026, 10, 1), custom_price=price
    )


def hold(lesson):
    Lesson.objects.filter(pk=lesson.pk).update(status="held")
    lesson.refresh_from_db()
    return services.accrue_lesson_charges(lesson)


def charged(enrollment):
    return -sum(LedgerEntry.objects.filter(enrollment=enrollment, kind="charge").values_list("amount", flat=True))


def test_custom_price_is_used_for_that_student_only(center):
    a, b, c = enroll(center, student("A")), enroll(center, student("B"), 200000, "kursning boshida"), enroll(center, student("C"), 300000)
    for lesson in center["lessons"]:
        hold(lesson)
    assert (charged(a), charged(b), charged(c)) == (350000, 200000, 300000)  # oy oxirida har kim o'z narxini to'laydi
    assert Enrollment.objects.get(pk=b.pk).price_note == "kursning boshida"


def test_effective_price_property(center):
    e = enroll(center, student("A"))
    assert e.effective_price == PRICE
    e.custom_price = 0
    assert e.effective_price == 0  # bepul ham narx: kurs narxiga o'tib ketmaydi


def test_zero_price_student_is_never_charged(center):
    free = enroll(center, student("Free"), 0)
    for lesson in center["lessons"][:3]:
        hold(lesson)
    assert charged(free) == 0 and not LedgerEntry.objects.filter(enrollment=free).exists()


def test_negative_or_huge_price_refused(center):
    with pytest.raises(ValidationError):
        enroll(center, student("A"), -1)
    with pytest.raises(ValidationError):
        enroll(center, student("B"), 2_000_000_000)


def test_changing_price_mid_month_corrects_charged_lessons_and_future_ones(center):
    e = plain_enroll(center, student("A"))
    for lesson in center["lessons"][:4]:
        hold(lesson)
    assert charged(e) == 140000  # 4 dars * 35 000
    created = services.set_enrollment_price(e, 200000, center["user"], "chegirma", today=date(2026, 10, 5))
    assert created == 1
    adj = LedgerEntry.objects.get(enrollment=e, kind="adjustment")
    assert adj.amount == 60000 and adj.period == date(2026, 10, 1) and adj.comment.startswith("Narx o'zgarishi:")  # 140 000 - 4*20 000
    assert services.get_balance(e.student, e.group) == -80000  # endi 4 dars * 20 000
    for lesson in center["lessons"][4:]:
        hold(lesson)
    assert services.get_balance(e.student, e.group) == -200000  # oy oxirida aniq yangi narx


def test_setting_the_same_price_twice_creates_no_second_adjustment(center):
    e = plain_enroll(center, student("A"))
    for lesson in center["lessons"][:4]:
        hold(lesson)
    services.set_enrollment_price(e, 200000, center["user"], today=date(2026, 10, 5))
    assert services.set_enrollment_price(e, 200000, center["user"], today=date(2026, 10, 6)) == 0
    assert LedgerEntry.objects.filter(enrollment=e, kind="adjustment").count() == 1


def test_price_can_go_back_to_course_price(center):
    e = plain_enroll(center, student("A"), 200000)
    for lesson in center["lessons"][:5]:
        hold(lesson)
    assert charged(e) == 100000
    services.set_enrollment_price(e, None, center["user"], today=date(2026, 10, 6))  # kurs narxiga qaytish
    e.refresh_from_db()
    assert e.custom_price is None and e.price_note == ""
    assert services.get_balance(e.student, e.group) == -5 * 35000  # 175 000


def test_month_breakdown_attributes_the_correction_to_the_corrected_month(center):
    e = plain_enroll(center, student("A"))
    for lesson in center["lessons"][:4]:
        hold(lesson)
    services.set_enrollment_price(e, 200000, center["user"], today=date(2026, 11, 3))  # tuzatish keyingi oyda kiritilsa ham
    rows = services.monthly_breakdown(e.student, e.group)
    assert [r["value"] for r in rows] == ["2026-10"] and rows[0]["balance"] == -80000


def test_price_change_is_logged_and_checked_by_role(center, teacher_user):
    e = enroll(center, student("A"))
    with pytest.raises(PermissionDenied):
        services.set_enrollment_price(e, 100000, teacher_user)
    services.set_enrollment_price(e, 250000, center["user"], "kelishuv")
    log = EnrollmentLog.objects.filter(enrollment=e, action="price").get()
    assert "350 000 -> 250 000" in log.note and "kelishuv" in log.note


def test_price_page_and_enroll_form(client, center, admin_user, teacher_user):
    e = enroll(center, student("A"))
    client.force_login(admin_user)
    page = client.get(reverse("groups:enrollment_price", args=[e.pk])).content.decode()
    assert "Shaxsiy narx" in page and "Kurs narxi" in page
    response = client.post(reverse("groups:enrollment_price", args=[e.pk]), {"price": "200 000", "note": "erta yozilgan"})
    assert response.status_code == 302
    e.refresh_from_db()
    assert e.custom_price == 200000 and e.price_note == "erta yozilgan"
    # Guruhga qo'shishda narx ko'rsatish
    other = student("B")
    client.post(reverse("groups:group_enroll", args=[center["group"].pk]), {"student": other.pk, "status": "active", "custom_price": "300 000"})
    assert Enrollment.objects.get(student=other).custom_price == 300000
    client.force_login(teacher_user)
    assert client.get(reverse("groups:enrollment_price", args=[e.pk])).status_code == 403


def test_prices_shown_on_group_and_student_pages(client, center, director_user):
    e = enroll(center, student("A"), 200000, "chegirma")
    client.force_login(director_user)
    group_page = client.get(reverse("groups:group_detail", args=[center["group"].pk])).content.decode()
    assert "chegirma" in group_page and reverse("groups:enrollment_price", args=[e.pk]) in group_page
    card = client.get(reverse("students:student_detail", args=[e.student.pk])).content.decode()
    assert "(shaxsiy)" in card and "Narx:" in card
