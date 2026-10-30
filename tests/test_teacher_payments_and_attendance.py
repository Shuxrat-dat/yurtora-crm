"""O'qituvchi o'z o'quvchilarining to'lovini ko'radi (qabul qila olmaydi); davomat telefonda oson belgilanadi."""

from datetime import date, time, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.attendance.models import Attendance
from apps.core.templatetags.mv import som
from apps.courses.models import Course
from apps.finance import services
from apps.finance.models import LedgerEntry
from apps.groups.models import Enrollment, Group
from apps.schedule.models import Lesson, Room
from apps.students.models import Student
from apps.teachers.models import Teacher

pytestmark = pytest.mark.django_db

DEBT = som(-20000)  # -50 000 + 30 000: faqat o'qituvchining o'z guruhi
FOREIGN = som(999000)


@pytest.fixture
def world(teacher_user, director_user):
    today = timezone.localdate()
    me = Teacher.objects.create(full_name="Men Ustoz", phone="+998900000091", user=teacher_user)
    other = Teacher.objects.create(full_name="Boshqa Ustoz", phone="+998900000092")
    course = Course.objects.create(name="Matematika", monthly_price=300000)
    mine = Group.objects.create(name="Mening guruhim", course=course, teacher=me, start_date=today - timedelta(days=30))
    theirs = Group.objects.create(name="Begona guruh", course=course, teacher=other, start_date=today - timedelta(days=30))
    room = Room.objects.create(name="1")
    a = Student.objects.create(first_name="Ali", last_name="Aliyev", phone="")
    b = Student.objects.create(first_name="Vali", last_name="Valiyev", phone="")
    start = today - timedelta(days=30)
    for st in (a, b):
        Enrollment.objects.create(student=st, group=mine, status="active", start_date=start, paid_from=start)
    Enrollment.objects.create(student=a, group=theirs, status="active", start_date=start, paid_from=start)
    # Ali: o'z guruhida 50 000 qarz va 30 000 to'lov; begona guruhda 999 000 qarz (o'qituvchi ko'rmasligi kerak)
    LedgerEntry.objects.create(student=a, group=mine, kind="charge", amount=-50000, entry_date=today, created_by=director_user)
    services.register_payment(a, mine, 30000, "cash", director_user, today)
    LedgerEntry.objects.create(student=a, group=theirs, kind="charge", amount=-999000, entry_date=today, created_by=director_user)
    lesson = Lesson.objects.create(group=mine, date=today, start_time=time(10), end_time=time(11), room=room, teacher=me)
    return {"a": a, "b": b, "mine": mine, "theirs": theirs, "lesson": lesson, "room": room, "me": me, "other": other}


def test_teacher_sees_own_group_balance_on_student_card_but_not_other_groups(client, teacher_user, world):
    client.force_login(teacher_user)
    html = client.get(reverse("students:student_detail", args=[world["a"].pk])).content.decode()
    assert 'data-tab="payments"' in html
    assert DEBT in html
    assert FOREIGN not in html and "Begona guruh" not in html
    assert "To'lov qabul qilish" not in html and "To&#x27;lov qabul qilish" not in html
    assert "data-pay-student" not in html
    assert reverse("finance:receipt", args=[LedgerEntry.objects.get(kind="payment").pk]) not in html


def test_teacher_student_list_shows_only_own_group_balance(client, teacher_user, world):
    client.force_login(teacher_user)
    html = client.get(reverse("students:student_list")).content.decode()
    assert "Balans" in html and DEBT in html
    assert som(1019000) not in html and FOREIGN not in html


def test_teacher_group_page_shows_balances_without_actions(client, teacher_user, world):
    client.force_login(teacher_user)
    html = client.get(reverse("groups:group_detail", args=[world["mine"].pk])).content.decode()
    assert DEBT in html
    assert "Narxni o&#x27;zgartirish" not in html and "Narxni o'zgartirish" not in html
    assert "Guruhdan chiqarish" not in html


def test_teacher_cannot_add_payment(client, teacher_user, world):
    client.force_login(teacher_user)
    a, mine = world["a"], world["mine"]
    assert client.get(reverse("finance:payment_modal_form", args=[a.pk])).status_code == 403
    response = client.post(reverse("finance:payment_add"), {
        "student_id": a.pk, "group_id": mine.pk, "amount": "100000", "method": "cash",
        "entry_date": timezone.localdate().isoformat(), "idempotency_key": "t1",
    })
    assert response.status_code == 403
    assert LedgerEntry.objects.filter(kind="payment").count() == 1


def test_mark_all_present_marks_only_unmarked_and_holds_lesson(client, teacher_user, world):
    lesson = world["lesson"]
    client.force_login(teacher_user)
    client.post(reverse("schedule:mark_attendance", args=[lesson.pk]), {"student_id": world["b"].pk, "status": "absent"})
    response = client.post(reverse("schedule:mark_all_present", args=[lesson.pk]), HTTP_HX_REQUEST="true")
    assert response.status_code == 200
    html = response.content.decode()
    assert 'id="att-block"' in html and "<b>2</b> / 2" in html
    # Sarlavha ham yangilanadi: holat "O'tildi", "Dars o'tildi" tugmasi yo'qoladi
    assert 'id="lesson-status" hx-swap-oob="true"' in html and 'id="lesson-held-action" hx-swap-oob="true"' in html
    assert reverse("schedule:mark_lesson_held", args=[lesson.pk]) not in html
    statuses = dict(Attendance.objects.filter(lesson=lesson).values_list("student_id", "status"))
    assert statuses == {world["a"].pk: "present", world["b"].pk: "absent"}  # avval belgilangani o'zgarmaydi
    lesson.refresh_from_db()
    assert lesson.status == "held"


def test_htmx_mark_returns_block_without_full_refresh(client, teacher_user, world):
    client.force_login(teacher_user)
    lesson = world["lesson"]
    response = client.post(
        reverse("schedule:mark_attendance", args=[lesson.pk]),
        {"student_id": world["a"].pk, "status": "late"},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200 and "HX-Refresh" not in response
    html = response.content.decode()
    # Dars o'tildi deb belgilandi va hisoblandi: oyning yagona darsi, to'liq oy narxi
    assert "<b>1</b> / 2" in html and "Dars o'tildi" in html and f"Qarz {som(-320000)}" in html


def test_lesson_page_has_big_buttons_and_today_link(client, teacher_user, world):
    client.force_login(teacher_user)
    lesson = world["lesson"]
    html = client.get(reverse("schedule:lesson_detail", args=[lesson.pk])).content.decode()
    assert "Hammasi keldi" in html and 'value="present"' in html and 'value="absent"' in html
    home = client.get(reverse("accounts:home")).content.decode()
    assert reverse("schedule:lesson_detail", args=[lesson.pk]) in home


def test_teacher_cannot_mark_other_teachers_lesson(client, teacher_user, world):
    foreign = Lesson.objects.create(
        group=world["theirs"], date=timezone.localdate(), start_time=time(12), end_time=time(13),
        room=world["room"], teacher=world["other"],
    )
    client.force_login(teacher_user)
    assert client.post(reverse("schedule:mark_all_present", args=[foreign.pk])).status_code == 403
    assert client.get(reverse("schedule:lesson_detail", args=[foreign.pk])).status_code == 403
    assert not Attendance.objects.filter(lesson=foreign).exists()


def test_future_lesson_cannot_be_marked(client, teacher_user, world):
    lesson = world["lesson"]
    Lesson.objects.filter(pk=lesson.pk).update(date=date.today() + timedelta(days=3))
    client.force_login(teacher_user)
    html = client.get(reverse("schedule:lesson_detail", args=[lesson.pk])).content.decode()
    assert "Hammasi keldi" not in html and "dars kuni" in html
