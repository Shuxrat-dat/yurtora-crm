"""Umumiy bot orqali ota-onalarga: davomat (kechiktirib, tuzatilsa bekor), to'lov, oylik hisobot."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.attendance.services import mark_attendance
from apps.courses.models import Course
from apps.finance import services as finance
from apps.groups.models import Enrollment, Group
from apps.notifications import events, telegram
from apps.notifications.models import NotificationLog, NotifySettings, TelegramContact
from apps.notifications.services import pending_count, process_pending_notifications
from apps.schedule.models import Lesson, Room
from apps.students.models import Student
from apps.teachers.models import Teacher

pytestmark = pytest.mark.django_db
TZ = ZoneInfo("Asia/Tashkent")


@pytest.fixture
def sent(monkeypatch):
    calls = []
    monkeypatch.setattr(telegram, "send_message", lambda chat_id, text, **kw: calls.append((chat_id, text)))
    return calls


@pytest.fixture
def world(director_user):
    today = timezone.localdate()
    teacher = Teacher.objects.create(full_name="Ustoz", phone="+998900000093")
    course = Course.objects.create(name="Matematika", monthly_price=300000)
    group = Group.objects.create(name="Mat-1", course=course, teacher=teacher, start_date=today - timedelta(days=40))
    room = Room.objects.create(name="1")
    student = Student.objects.create(first_name="Ali", last_name="Aliyev", phone="")
    start = today - timedelta(days=70)
    Enrollment.objects.create(student=student, group=group, status="active", start_date=start, paid_from=start)
    TelegramContact.objects.create(student=student, chat_id=777)
    lesson = Lesson.objects.create(group=group, date=today, start_time=time(10), end_time=time(11), room=room, teacher=teacher)
    return {"student": student, "group": group, "lesson": lesson, "user": director_user, "room": room, "teacher": teacher}


def mark(world, status, capture, lesson=None):
    with capture(execute=True):
        return mark_attendance(lesson or world["lesson"], world["student"], status, world["user"])


def test_absent_is_queued_with_delay_and_sent_later(world, sent, django_capture_on_commit_callbacks):
    mark(world, "absent", django_capture_on_commit_callbacks)
    log = NotificationLog.objects.get(kind="attendance")
    assert log.status == "pending" and log.send_after > timezone.now() + timedelta(minutes=9)
    assert pending_count() == 0  # hali vaqti kelmagan
    process_pending_notifications(pause=0)
    assert sent == []
    NotificationLog.objects.filter(pk=log.pk).update(send_after=timezone.now() - timedelta(seconds=1))
    process_pending_notifications(pause=0)
    [(chat, text)] = sent
    assert chat == 777 and "Aliyev Ali" in text and "kelmadi" in text and "Mat-1" in text


def test_corrected_to_present_cancels_message(world, sent, django_capture_on_commit_callbacks):
    mark(world, "absent", django_capture_on_commit_callbacks)
    mark(world, "present", django_capture_on_commit_callbacks)  # o'qituvchi xatoni tuzatdi
    NotificationLog.objects.update(send_after=timezone.now() - timedelta(seconds=1))
    process_pending_notifications(pause=0)
    assert sent == []
    assert NotificationLog.objects.get(kind="attendance").status == "cancelled"


def test_absent_then_late_sends_one_message_with_latest_status(world, sent, django_capture_on_commit_callbacks):
    mark(world, "absent", django_capture_on_commit_callbacks)
    mark(world, "late", django_capture_on_commit_callbacks)
    assert NotificationLog.objects.filter(kind="attendance").count() == 1
    NotificationLog.objects.update(send_after=timezone.now() - timedelta(seconds=1))
    process_pending_notifications(pause=0)
    [(_, text)] = sent
    assert "kechikib keldi" in text


def test_present_old_lessons_and_disabled_setting_send_nothing(world, sent, django_capture_on_commit_callbacks):
    mark(world, "present", django_capture_on_commit_callbacks)
    old = Lesson.objects.create(
        group=world["group"], date=timezone.localdate() - timedelta(days=5), start_time=time(10), end_time=time(11),
        room=world["room"], teacher=world["teacher"],
    )
    mark(world, "absent", django_capture_on_commit_callbacks, lesson=old)  # orqaga qarab belgilash
    s = NotifySettings.get()
    s.attendance = False
    s.save()
    other = Lesson.objects.create(
        group=world["group"], date=timezone.localdate(), start_time=time(15), end_time=time(16),
        room=world["room"], teacher=world["teacher"],
    )
    mark(world, "absent", django_capture_on_commit_callbacks, lesson=other)
    assert not NotificationLog.objects.filter(kind="attendance").exists()


def test_payment_message_has_amount_month_and_balance(world, sent, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        finance.register_payment(
            world["student"], world["group"], 300000, "cash", world["user"], timezone.localdate(),
            period=timezone.localdate().replace(day=1),
        )
    process_pending_notifications(pause=0)
    [(chat, text)] = sent
    assert chat == 777 and "to'lov qabul qilindi" in text and "300 000 so'm" in text and " uchun)" in text
    assert "Oldindan to'langan: 300 000" in text
    assert NotificationLog.objects.get(kind="payment").text != text  # yuborilgandan keyin matn saqlanmaydi


def test_payment_setting_off(world, sent, django_capture_on_commit_callbacks):
    s = NotifySettings.get()
    s.payment = False
    s.save()
    with django_capture_on_commit_callbacks(execute=True):
        finance.register_payment(world["student"], world["group"], 100000, "cash", world["user"], timezone.localdate())
    assert not NotificationLog.objects.filter(kind="payment").exists()


def test_notification_error_never_breaks_payment(world, monkeypatch, django_capture_on_commit_callbacks):
    def boom(cls):
        raise RuntimeError("x")

    monkeypatch.setattr(NotifySettings, "get", classmethod(boom))
    with django_capture_on_commit_callbacks(execute=True):
        entry = finance.register_payment(world["student"], world["group"], 100000, "cash", world["user"], timezone.localdate())
    assert entry.pk


def test_monthly_report_once_on_first_days(world, sent, django_capture_on_commit_callbacks):
    last_month = (timezone.localdate().replace(day=1) - timedelta(days=1)).replace(day=10)
    lesson = Lesson.objects.create(
        group=world["group"], date=last_month, start_time=time(10), end_time=time(11),
        room=world["room"], teacher=world["teacher"], status="held",
    )
    mark(world, "present", django_capture_on_commit_callbacks, lesson=lesson)
    first = timezone.localdate().replace(day=1)
    assert events.maybe_send_monthly_reports(datetime.combine(first, time(9), TZ)) == 0  # soat 10 gacha emas
    assert events.maybe_send_monthly_reports(datetime.combine(first + timedelta(days=5), time(12), TZ)) == 0  # kech
    assert events.maybe_send_monthly_reports(datetime.combine(first, time(11), TZ)) == 1
    assert events.maybe_send_monthly_reports(datetime.combine(first, time(12), TZ)) == 0  # ikkinchi marta yo'q
    process_pending_notifications(pause=0)
    [(_, text)] = [m for m in sent if "hisoboti" in m[1]]
    assert "Mat-1" in text and "Davomat: 100%" in text and "keldi 1" in text


def test_settings_page_saves_toggles(client, admin_user, world):
    client.force_login(admin_user)
    page = client.get(reverse("notifications:notify_settings"))
    assert page.status_code == 200 and "Oylik hisobot" in page.content.decode()
    client.post(reverse("notifications:notify_settings"), {"payment": "on"})
    s = NotifySettings.get()
    assert (s.attendance, s.payment, s.monthly) == (False, True, False)


def test_teacher_cannot_open_settings(client, teacher_user):
    client.force_login(teacher_user)
    assert client.get(reverse("notifications:notify_settings")).status_code == 403

