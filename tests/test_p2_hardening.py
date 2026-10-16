"""P2: открытый редирект, гонка кредита, сессии, zip-бомба, категории расходов, смена учителя, повторная отправка, ПДн в журнале."""

import io
import zipfile
from datetime import date, time

import pytest
from django.conf import settings
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.courses.models import Course
from apps.finance import services as finance
from apps.finance.models import ExpenseCategory, LedgerEntry
from apps.groups.models import Enrollment, Group
from apps.notifications import services as notify
from apps.notifications import telegram
from apps.notifications.models import NotificationLog, TelegramContact
from apps.schedule.models import Lesson, Room
from apps.schedule.services import reassign_future_lessons
from apps.students import importer
from apps.students.models import Student
from apps.teachers.models import Teacher

pytestmark = pytest.mark.django_db
EVIL = ["//evil.example/x", "https://evil.example/x", "/\\evil.example", "javascript:alert(1)", "///evil.example"]


@pytest.fixture
def world():
    teacher = Teacher.objects.create(full_name="Ustoz", phone="+998900000000")
    other = Teacher.objects.create(full_name="Yangi", phone="+998900000001")
    course = Course.objects.create(name="English", monthly_price=400000)
    group = Group.objects.create(name="ENG-1", course=course, teacher=teacher, start_date=date(2026, 1, 1), max_students=20)
    room, room2 = Room.objects.create(name="101"), Room.objects.create(name="102")
    student = Student.objects.create(first_name="Ali", last_name="Valiyev", phone="+998901112233")
    Enrollment.objects.create(student=student, group=group, status="active", start_date=date(2026, 3, 1), paid_from=date(2026, 3, 1))
    return {"teacher": teacher, "other": other, "course": course, "group": group, "room": room, "room2": room2, "student": student}


# ---------- SEC-03 ----------
@pytest.mark.parametrize("target", EVIL)
def test_open_redirect_is_blocked_everywhere(client, director_user, world, target):
    client.force_login(director_user)
    enrollment = Enrollment.objects.get(student=world["student"], group=world["group"])
    posts = [
        (reverse("finance:payment_create", args=[world["student"].pk, world["group"].pk]), {"amount": "5000", "method": "cash", "entry_date": "2026-03-10"}),
        (reverse("groups:group_freeze", args=[world["group"].pk, enrollment.pk]), {}),
        (reverse("groups:group_remove", args=[world["group"].pk, world["student"].pk]), {}),
    ]
    for url, data in posts:
        response = client.post(url, {**data, "next": target})
        assert response.status_code == 302 and "evil.example" not in response["Location"] and not response["Location"].startswith("javascript")


def test_local_next_is_still_honoured(client, director_user, world):
    client.force_login(director_user)
    response = client.post(
        reverse("finance:payment_create", args=[world["student"].pk, world["group"].pk]),
        {"amount": "5000", "method": "cash", "entry_date": "2026-03-10", "next": "/students/?page=2"},
    )
    assert response["Location"] == "/students/?page=2"


# ---------- F-11 ----------
def test_second_excused_credit_is_a_clear_error_even_when_the_pre_check_is_bypassed(director_user, world, monkeypatch):
    from apps.schedule import services as schedule

    lesson = Lesson.objects.create(group=world["group"], date=date(2026, 3, 9), start_time=time(9), end_time=time(10), room=world["room"], teacher=world["teacher"])
    schedule.mark_lesson_held(lesson, director_user)
    charge = LedgerEntry.objects.get(kind="charge")
    finance.grant_excused_credit(charge, director_user, "bemor")
    real_filter = LedgerEntry.objects.filter

    class NoExisting:
        def first(self):
            return None

    def fake_filter(*args, **kwargs):
        if kwargs.get("kind") == "excused_credit" and "reverses" in kwargs:
            return NoExisting()  # имитируем гонку: проверка «уже есть» ничего не нашла
        return real_filter(*args, **kwargs)

    monkeypatch.setattr(LedgerEntry.objects, "filter", fake_filter)
    with pytest.raises(ValidationError, match="allaqachon"):
        finance.grant_excused_credit(charge, director_user, "yana")
    monkeypatch.undo()
    assert LedgerEntry.objects.filter(kind="excused_credit").count() == 1


# ---------- SEC-08 ----------
def test_session_has_idle_timeout():
    assert settings.SESSION_COOKIE_AGE == 12 * 60 * 60 and settings.SESSION_SAVE_EVERY_REQUEST is True


# ---------- SEC-11 ----------
def make_zip(entries, size=10):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for i in range(entries):
            archive.writestr(f"f{i}.txt", "0" * size)
    buffer.seek(0)
    return buffer


def test_zip_bomb_and_entry_flood_are_rejected_before_loading():
    bomb = make_zip(1, size=40 * 1024 * 1024)  # сжимается в крошечный файл
    assert bomb.getbuffer().nbytes < importer.MAX_FILE_SIZE
    result = importer.parse_students(bomb)
    assert result.errors and "hajmi" in result.errors[0][1]
    flood = importer.parse_students(make_zip(importer.MAX_ZIP_ENTRIES + 1))
    assert flood.errors and "hajmi" in flood.errors[0][1]


def test_normal_template_still_imports():
    result = importer.parse_students(io.BytesIO(importer.build_template()))
    assert not result.errors


# ---------- UI-02 ----------
def test_default_expense_categories_come_from_migration_not_from_a_get(client, director_user):
    names = set(ExpenseCategory.objects.filter(is_system=True).values_list("name", flat=True))
    assert {"Avans", "Marketing", "Ijara va kommunal"} <= names
    before = ExpenseCategory.objects.count()
    client.force_login(director_user)
    assert client.get(reverse("finance:dashboard")).status_code == 200
    assert ExpenseCategory.objects.count() == before  # GET ничего не пишет


# ---------- SCH-03 ----------
def lesson_on(world, day, status="scheduled", start=9):
    return Lesson.objects.create(group=world["group"], date=date(2026, 11, day), start_time=time(start), end_time=time(start + 1), room=world["room"], teacher=world["teacher"], status=status)


def test_teacher_change_moves_only_future_scheduled_lessons(world):
    future, past_held, cancelled = lesson_on(world, 20), lesson_on(world, 2, "held", start=10), lesson_on(world, 25, "cancelled", start=11)
    assert reassign_future_lessons(world["group"], world["other"], today=date(2026, 11, 10)) == (1, 0)
    for obj, expected in ((future, "other"), (past_held, "teacher"), (cancelled, "teacher")):
        obj.refresh_from_db()
        assert obj.teacher == world[expected]


def test_teacher_change_reports_time_conflicts(world):
    other_group = Group.objects.create(name="X", course=world["course"], teacher=world["other"], start_date=date(2026, 1, 1))
    Lesson.objects.create(group=other_group, date=date(2026, 11, 20), start_time=time(9), end_time=time(10), room=world["room2"], teacher=world["other"])
    lesson_on(world, 20)
    assert reassign_future_lessons(world["group"], world["other"], today=date(2026, 11, 10)) == (1, 1)


def test_group_edit_form_applies_the_teacher_change(client, admin_user, world):
    from django.utils import timezone

    today = timezone.localdate()
    future = Lesson.objects.create(group=world["group"], date=today.replace(year=today.year + 1), start_time=time(9), end_time=time(10), room=world["room"], teacher=world["teacher"])
    client.force_login(admin_user)
    group = world["group"]
    client.post(reverse("groups:group_edit", args=[group.pk]), {"name": group.name, "course": world["course"].pk, "teacher": world["other"].pk, "max_students": 20, "start_date": "2026-01-01", "status": "active"})
    future.refresh_from_db()
    assert future.teacher == world["other"]


# ---------- BOT-04 + SEC-10 ----------
@pytest.fixture
def telegram_ready(settings, monkeypatch):
    settings.TELEGRAM_BOT_TOKEN = "123:TEST"
    settings.TELEGRAM_REMINDER_COOLDOWN_DAYS = 3
    sent = []
    monkeypatch.setattr(telegram, "send_message", lambda chat_id, text, reply_markup=None: sent.append((chat_id, text)))
    return sent


def make_debtor(world, director_user):
    finance.add_adjustment(world["student"], world["group"], -300000, director_user, "qarz")
    TelegramContact.objects.create(student=world["student"], chat_id=555)


def test_manual_reminder_respects_the_cooldown(client, admin_user, director_user, world, telegram_ready):
    make_debtor(world, director_user)
    client.force_login(admin_user)
    url = reverse("notifications:student_telegram", args=[world["student"].pk])
    for _ in range(3):
        client.post(url, {"action": "send_reminder"})
    assert len(telegram_ready) == 1


def test_delivered_messages_do_not_keep_names_and_amounts_in_the_log(director_user, world, telegram_ready):
    make_debtor(world, director_user)
    groups = next(iter(notify.debts_by_student()))["groups"]
    notify.queue_debt_reminder(world["student"], groups)
    assert "Valiyev" in NotificationLog.objects.get().text  # в очереди текст нужен
    notify.process_pending_notifications(pause=0)
    log = NotificationLog.objects.get()
    assert log.status == "sent" and "Valiyev" not in log.text and "300" not in log.text
    assert "Valiyev" in telegram_ready[0][1]  # а родитель получил полное сообщение
