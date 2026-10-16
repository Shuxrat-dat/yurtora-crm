"""F-04/F-05/F-06: сторно при отмене, выравнивание месяца до цены курса, честная обработка ошибок начисления."""

from datetime import date, time

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.courses.models import Course
from apps.finance import services as finance
from apps.finance.models import LedgerEntry
from apps.groups import services as groups
from apps.groups.models import Enrollment, Group
from apps.schedule import services as schedule
from apps.schedule.models import Lesson, Room
from apps.students.models import Student
from apps.teachers.models import Teacher

pytestmark = pytest.mark.django_db
PRICE = 800000


@pytest.fixture
def world(admin_user):
    teacher = Teacher.objects.create(full_name="Ustoz", phone="+998900000000")
    course = Course.objects.create(name="English", monthly_price=PRICE)
    group = Group.objects.create(name="ENG-1", course=course, teacher=teacher, start_date=date(2026, 1, 1))
    room = Room.objects.create(name="101")

    def student(n):
        return Student.objects.create(first_name=f"S{n}", last_name="Probe", phone=f"+99890000{n:04d}")

    def lesson(day, status="scheduled"):
        return Lesson.objects.create(
            group=group, date=date(2026, 3, day), start_time=time(14), end_time=time(15), room=room, teacher=teacher, status=status
        )

    return {"group": group, "student": student, "lesson": lesson, "admin": admin_user, "teacher": teacher}


def charged(student):
    return -sum(e.amount for e in LedgerEntry.objects.filter(student=student, kind="charge"))


def balance(student):
    return finance.get_balance(student)


def test_month_total_equals_price_when_lessons_cancelled_midway(world):
    """F-05: 4 урока проведены при n=8, 2 отменены, остальные проведены — итог ровно цена курса."""
    s = world["student"](1)
    groups.enroll_student(s, world["group"], start_date=date(2026, 3, 1), user=world["admin"])
    lessons = [world["lesson"](d) for d in (2, 4, 6, 9, 11, 13, 16, 18)]
    for lesson in lessons[:4]:
        schedule.mark_lesson_held(lesson, world["admin"])
    schedule.cancel_lesson(lessons[6], "x", user=world["admin"])
    schedule.cancel_lesson(lessons[7], "x", user=world["admin"])
    for lesson in lessons[4:6]:
        schedule.mark_lesson_held(lesson, world["admin"])
    assert charged(s) == PRICE


def test_regular_full_month_unchanged(world):
    s = world["student"](2)
    groups.enroll_student(s, world["group"], start_date=date(2026, 3, 1), user=world["admin"])
    for lesson in [world["lesson"](d) for d in (2, 4, 6, 9, 11, 13, 16)]:
        schedule.mark_lesson_held(lesson, world["admin"])
    assert charged(s) == PRICE  # 7 уроков: 7 равных-по-формуле долей


def test_cancelling_held_lesson_reverses_charges(world):
    """F-04: отмена проведённого урока возвращает начисленное и связывает сторно с начислением."""
    a, b = world["student"](3), world["student"](4)
    for st in (a, b):
        groups.enroll_student(st, world["group"], start_date=date(2026, 3, 1), user=world["admin"])
    lesson, other = world["lesson"](2), world["lesson"](4)
    schedule.mark_lesson_held(lesson, world["admin"])
    assert balance(a) < 0 and balance(b) < 0
    schedule.cancel_lesson(lesson, "учитель заболел", user=world["admin"])
    assert balance(a) == 0 and balance(b) == 0
    reversals = LedgerEntry.objects.filter(lesson=lesson, kind="adjustment")
    assert reversals.count() == 2
    assert all(r.reverses.kind == "charge" and r.created_by == world["admin"] for r in reversals)
    assert other.status == "scheduled"


def test_cancel_skips_charges_already_credited_as_excused(world):
    s = world["student"](5)
    groups.enroll_student(s, world["group"], start_date=date(2026, 3, 1), user=world["admin"])
    lesson = world["lesson"](2)
    schedule.mark_lesson_held(lesson, world["admin"])
    charge = LedgerEntry.objects.get(student=s, kind="charge")
    from apps.accounts.models import User

    director = User.objects.create_user("dir", password="x", role="director")
    finance.grant_excused_credit(charge, director, "bemor")
    assert balance(s) == 0
    schedule.cancel_lesson(lesson, "x", user=world["admin"])
    assert balance(s) == 0  # второй раз не вернули


def test_cancel_of_scheduled_lesson_creates_no_entries(world):
    s = world["student"](6)
    groups.enroll_student(s, world["group"], start_date=date(2026, 3, 1), user=world["admin"])
    lesson = world["lesson"](2)
    schedule.cancel_lesson(lesson, "x", user=world["admin"])
    assert not LedgerEntry.objects.exists()
    with pytest.raises(ValidationError):
        schedule.cancel_lesson(lesson, "x", user=world["admin"])  # повторная отмена


def test_marking_held_twice_is_rejected_and_view_shows_error(client, world):
    lesson = world["lesson"](2)
    schedule.mark_lesson_held(lesson, world["admin"])
    with pytest.raises(ValidationError, match="allaqachon"):
        schedule.mark_lesson_held(lesson, world["admin"])
    client.force_login(world["admin"])
    response = client.post(reverse("schedule:mark_lesson_held", args=[lesson.pk]), follow=True)
    texts = [str(m) for m in response.context["messages"]]
    assert any("allaqachon" in t for t in texts) and not any("hisob qilindi" in t for t in texts)


def test_accrual_failure_is_not_swallowed(world, monkeypatch):
    """F-06: раньше любая ошибка начисления скрывалась, а урок оставался «проведённым» без долга."""
    s = world["student"](7)
    groups.enroll_student(s, world["group"], start_date=date(2026, 3, 1), user=world["admin"])
    lesson = world["lesson"](2)

    def boom(*args, **kwargs):
        raise RuntimeError("accrual failed")

    monkeypatch.setattr(finance, "accrue_lesson_charges", boom)
    with pytest.raises(RuntimeError):
        schedule.mark_lesson_held(lesson, world["admin"])
    lesson.refresh_from_db()
    assert lesson.status == "scheduled"  # откат: статус не изменился


def test_backdated_enrollment_gets_charges_for_lessons_already_held(world):
    early = world["student"](8)
    groups.enroll_student(early, world["group"], start_date=date(2026, 3, 1), user=world["admin"])
    lessons = [world["lesson"](d) for d in (2, 4, 6, 9)]
    for lesson in lessons:
        schedule.mark_lesson_held(lesson, world["admin"])

    late = world["student"](9)
    enrollment = groups.enroll_student(late, world["group"], start_date=date(2026, 3, 4), user=world["admin"])
    assert enrollment.backfilled_charges == 3  # уроки 4, 6 и 9 марта
    assert LedgerEntry.objects.filter(student=late, kind="charge").count() == 3
    # повторная привязка не дублирует
    assert finance.backfill_enrollment_charges(Enrollment.objects.get(pk=enrollment.pk)) == 0


def test_enrollment_from_today_has_nothing_to_backfill(world):
    from django.utils import timezone

    s = world["student"](10)
    world["lesson"](2)  # прошлый урок, не проведён
    enrollment = groups.enroll_student(s, world["group"], start_date=timezone.localdate(), user=world["admin"])
    assert enrollment.backfilled_charges == 0


def test_trial_enrollment_is_not_backfilled(world):
    s = world["student"](11)
    schedule.mark_lesson_held(world["lesson"](2), world["admin"])
    enrollment = groups.enroll_student(s, world["group"], start_date=date(2026, 3, 1), status="trial", user=world["admin"])
    assert enrollment.backfilled_charges == 0
    assert not LedgerEntry.objects.filter(student=s).exists()


def test_unfreeze_does_not_charge_the_frozen_period(world):
    s = world["student"](12)
    enrollment = groups.enroll_student(s, world["group"], start_date=date(2026, 3, 1), user=world["admin"])
    Enrollment.objects.filter(pk=enrollment.pk).update(status="frozen", frozen_from=date(2026, 3, 3))
    schedule.mark_lesson_held(world["lesson"](9), world["admin"])  # урок в период заморозки
    enrollment.refresh_from_db()
    groups.unfreeze_enrollment(enrollment, user=world["admin"])
    assert not LedgerEntry.objects.filter(student=s, kind="charge").exists()
