"""Tests: teacher profile page (data blocks, access rules, no salary data)."""

from datetime import date, datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.attendance.models import Attendance
from apps.courses.models import Course
from apps.groups.models import Enrollment, Group
from apps.schedule.models import Lesson, Room, ScheduleSlot
from apps.students.models import Student
from apps.teachers.models import Teacher
from apps.teachers.services import (
    QUEUE_ATTENDANCE,
    QUEUE_STATUS,
    build_teacher_profile,
)

OLD = date(2026, 1, 1)
NOW = timezone.make_aware(datetime(2026, 3, 10, 11, 0))  # seshanba


def _student(first, last, **extra):
    return Student.objects.create(
        first_name=first, last_name=last, phone="+998900000000", joined_date=OLD, **extra
    )


def _enroll(student, group):
    return Enrollment.objects.create(
        student=student, group=group, status=Enrollment.Status.ACTIVE,
        start_date=OLD, paid_from=OLD,
    )


def _lesson(group, teacher, room, day, start="10:00", end="11:00", status=Lesson.Status.HELD):
    return Lesson.objects.create(
        group=group, date=day, start_time=start, end_time=end, room=room,
        teacher=teacher, status=status,
    )


@pytest.fixture
def world(db, teacher_user):
    other_user = User.objects.create_user(username="t2", password="x", role=User.Role.TEACHER)
    teacher = Teacher.objects.create(user=teacher_user, full_name="Sardor Toshmatov", phone="+998 95 000-57-86")
    other = Teacher.objects.create(user=other_user, full_name="Other Teacher", phone="+998902")
    course = Course.objects.create(name="English", level="A1", monthly_price=400000)
    group_a = Group.objects.create(name="Alpha", course=course, teacher=teacher, start_date=OLD)
    group_closed = Group.objects.create(
        name="OldGroup", course=course, teacher=teacher, start_date=OLD, status=Group.Status.CLOSED
    )
    group_other = Group.objects.create(name="Gamma", course=course, teacher=other, start_date=OLD)
    s1, s2, s3 = _student("Ali", "Aliyev"), _student("Vali", "Valiyev"), _student("Sami", "Samiyev")
    archived = _student("Arx", "Ivov", status=Student.Status.ARCHIVED)
    for student in (s1, s2, s3, archived):
        _enroll(student, group_a)
    room = Room.objects.create(name="R1")
    return {
        "teacher": teacher, "other": other, "group_a": group_a, "group_closed": group_closed,
        "group_other": group_other, "students": (s1, s2, s3), "room": room, "course": course,
    }


@pytest.mark.django_db
class TestProfileData:
    def test_info_counts_only_active_groups_and_active_students(self, world):
        info = build_teacher_profile(world["teacher"], now=NOW)["info"]
        assert info["courses_count"] == 1
        assert info["groups_count"] == 1          # yopilgan guruh hisoblanmaydi
        assert info["students_count"] == 3        # arxivlangan o'quvchi hisoblanmaydi
        assert info["phone"] == "+998 95 000-57-86"
        assert info["branch"] == "O'quv markazi"  # "Chek sozlamalari" -> "Markaz nomi" (standart)

    def test_work_queue_lists_unmarked_attendance_and_unconfirmed_lessons(self, world):
        teacher, group, room = world["teacher"], world["group_a"], world["room"]
        s1, s2, s3 = world["students"]
        held = _lesson(group, teacher, room, date(2026, 3, 9))
        Attendance.objects.create(lesson=held, student=s1, status=Attendance.Status.PRESENT)
        past_scheduled = _lesson(group, teacher, room, date(2026, 3, 8), status=Lesson.Status.SCHEDULED)
        _lesson(group, teacher, room, date(2026, 3, 7), status=Lesson.Status.CANCELLED)
        _lesson(world["group_other"], world["other"], room, date(2026, 3, 9))        # boshqa o'qituvchi
        _lesson(group, teacher, room, date(2026, 2, 1))                               # 14 kundan eski
        _lesson(group, teacher, room, date(2026, 3, 10), "15:00", "16:00", Lesson.Status.SCHEDULED)  # bugun, hali o'tmagan

        queue = build_teacher_profile(teacher, now=NOW)["queue"]
        by_kind = {item["kind"]: item for item in queue}
        assert len(queue) == 2
        assert by_kind[QUEUE_ATTENDANCE]["lesson"] == held and by_kind[QUEUE_ATTENDANCE]["count"] == 2
        assert by_kind[QUEUE_STATUS]["lesson"] == past_scheduled

        for student in (s2, s3):
            Attendance.objects.create(lesson=held, student=student, status=Attendance.Status.ABSENT)
        queue = build_teacher_profile(teacher, now=NOW)["queue"]
        assert [item["kind"] for item in queue] == [QUEUE_STATUS]

    def test_attention_lists_students_with_three_or_more_recent_absences(self, world):
        teacher, group, room = world["teacher"], world["group_a"], world["room"]
        s1, s2, s3 = world["students"]
        for day in (1, 2, 3):                                      # s1: 3 ta (oxirgi 30 kun)
            lesson = _lesson(group, teacher, room, date(2026, 3, day))
            Attendance.objects.create(lesson=lesson, student=s1, status=Attendance.Status.ABSENT)
            if day < 3:                                            # s2: faqat 2 ta
                Attendance.objects.create(lesson=lesson, student=s2, status=Attendance.Status.ABSENT)
        for day in (10, 11, 12):                                   # s3: 3 ta, lekin 30 kundan eski
            lesson = _lesson(group, teacher, room, date(2026, 1, day))
            Attendance.objects.create(lesson=lesson, student=s3, status=Attendance.Status.ABSENT)

        attention = build_teacher_profile(teacher, now=NOW)["attention"]
        assert [(row["name"], row["absences"], row["group"]) for row in attention] == [
            ("Aliyev Ali", 3, "Alpha")
        ]

    def test_current_and_next_lesson(self, world):
        teacher, group, room = world["teacher"], world["group_a"], world["room"]
        assert build_teacher_profile(teacher, now=NOW)["lesson_now"] is None

        today_lesson = _lesson(group, teacher, room, date(2026, 3, 10), "10:00", "12:00", Lesson.Status.SCHEDULED)
        current = build_teacher_profile(teacher, now=NOW)["lesson_now"]
        assert current["state"] == "current" and current["lesson"] == today_lesson

        tomorrow = _lesson(group, teacher, room, date(2026, 3, 11), "09:00", "10:00", Lesson.Status.SCHEDULED)
        later = timezone.make_aware(datetime(2026, 3, 10, 12, 30))
        nxt = build_teacher_profile(teacher, now=later)["lesson_now"]
        assert nxt["state"] == "next" and nxt["lesson"] == tomorrow

    def test_groups_and_archive_toggle(self, world):
        ScheduleSlot.objects.create(
            group=world["group_a"], weekday=0, start_time="10:00", end_time="11:00", room=world["room"]
        )
        active = build_teacher_profile(world["teacher"], now=NOW)["groups"]
        assert [(g["group"].name, g["students_count"], g["schedule"]) for g in active] == [
            ("Alpha", 4, "Du 10:00")  # 4 ta faol a'zolik (arxivlangan o'quvchi a'zoligi ham 'active')
        ]
        archive = build_teacher_profile(world["teacher"], show_archive=True, now=NOW)["groups"]
        assert [g["group"].name for g in archive] == ["OldGroup"]


@pytest.mark.django_db
class TestProfileView:
    def test_teacher_sees_own_profile_without_salary_data(self, client, teacher_user, world):
        client.force_login(teacher_user)
        response = client.get(reverse("teachers:my_profile"))
        html = response.content.decode()
        assert response.status_code == 200
        for text in ("Mening ma'lumotlarim", "Sardor Toshmatov", "Ish navbati", "Diqqat talab qiladi",
                     "Guruhlar", "Navbat bo'sh", "Kontakt", "+998 95 000-57-86"):
            assert text in html
        lowered = html.lower()
        assert "oylik" not in lowered and "maosh" not in lowered and "monthly_salary" not in lowered

    def test_teacher_cannot_open_another_teachers_profile(self, client, teacher_user, world):
        client.force_login(teacher_user)
        assert client.get(reverse("teachers:teacher_profile", args=[world["other"].pk])).status_code == 403
        assert client.get(reverse("teachers:teacher_profile", args=[world["teacher"].pk])).status_code == 200

    def test_administrator_and_director_can_open_any_profile(self, client, admin_user, director_user, world):
        for user in (admin_user, director_user):
            client.force_login(user)
            response = client.get(reverse("teachers:teacher_profile", args=[world["other"].pk]))
            assert response.status_code == 200
            assert "O'qituvchi ma'lumotlari" in response.content.decode()
            assert "oylik" not in response.content.decode().lower()

    def test_anonymous_is_redirected_to_login(self, client, world):
        response = client.get(reverse("teachers:my_profile"))
        assert response.status_code == 302 and reverse("accounts:login") in response["Location"]

    def test_user_without_teacher_profile_is_redirected_home(self, client, director_user, db):
        client.force_login(director_user)
        response = client.get(reverse("teachers:my_profile"))
        assert response.status_code == 302 and response["Location"] == reverse("accounts:home")

    def test_archive_switch_shows_closed_groups_only(self, client, teacher_user, world):
        client.force_login(teacher_user)
        default = client.get(reverse("teachers:my_profile")).content.decode()
        assert "Alpha" in default and "OldGroup" not in default
        archive = client.get(reverse("teachers:my_profile"), {"archive": "1"}).content.decode()
        assert "OldGroup" in archive and "Arxivdagi guruhlar" in archive and "Alpha" not in archive

    def test_menu_link_for_teacher_only_and_profile_link_in_list(self, client, teacher_user, admin_user, world):
        client.force_login(teacher_user)
        assert reverse("teachers:my_profile") in client.get(reverse("accounts:home")).content.decode()
        client.force_login(admin_user)
        assert reverse("teachers:my_profile") not in client.get(reverse("accounts:home")).content.decode()
        listing = client.get(reverse("teachers:teacher_list")).content.decode()
        assert reverse("teachers:teacher_profile", args=[world["teacher"].pk]) in listing


class TestTeacherListAndCards:
    """Soff uslubidagi jadval va guruh kartalari."""

    def test_list_shows_pay_only_to_director(self, client, director_user, admin_user, world):
        Teacher.objects.filter(pk=world["teacher"].pk).update(pay_type="fixed", monthly_salary=3_500_000)
        url = reverse("teachers:teacher_list")
        client.force_login(director_user)
        assert "3 500 000" in client.get(url).content.decode().replace("\xa0", " ")
        client.force_login(admin_user)
        body = client.get(url).content.decode().replace("\xa0", " ")
        assert "3 500 000" not in body and "Belgilangan oylik" not in body

    def test_list_search_by_name_and_phone(self, client, director_user, world):
        client.force_login(director_user)
        url = reverse("teachers:teacher_list")
        name = world["teacher"].full_name.split()[0]
        assert world["teacher"].full_name in client.get(url, {"q": name}).content.decode()
        assert world["teacher"].full_name not in client.get(url, {"q": "zzzz-yo'q"}).content.decode()

    def test_group_cards_show_counts_months_and_rooms(self, client, admin_user, world):
        client.force_login(admin_user)
        body = client.get(reverse("teachers:teacher_profile", args=[world["teacher"].pk])).content.decode()
        assert "Jami" in body and "Faol" in body and " oy" in body
