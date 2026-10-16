"""Tests for Stage 1b: Role-based access control across student, teacher, course, and group screens."""

from datetime import date

import pytest
from django.urls import reverse

from apps.courses.models import Course
from apps.groups.models import Enrollment, Group
from apps.students.models import Student
from apps.teachers.models import Teacher


@pytest.fixture
def course(db):
    return Course.objects.create(
        name="Ingliz tili", level="IELTS", monthly_price=600000
    )


@pytest.fixture
def teacher_profile_1(db, teacher_user):
    return Teacher.objects.create(
        full_name="O'qituvchi Bir",
        phone="+998901111111",
        user=teacher_user,
    )


@pytest.fixture
def other_teacher_user(db, password):
    from apps.accounts.models import User

    return User.objects.create_user(
        username="other_teacher",
        email="other_teacher@example.test",
        password=password,
        role=User.Role.TEACHER,
    )


@pytest.fixture
def teacher_profile_2(db, other_teacher_user):
    return Teacher.objects.create(
        full_name="O'qituvchi Ikki",
        phone="+998902222222",
        user=other_teacher_user,
    )


@pytest.fixture
def group_teacher_1(db, course, teacher_profile_1):
    return Group.objects.create(
        name="Group-T1",
        course=course,
        teacher=teacher_profile_1,
        max_students=10,
        start_date=date(2026, 10, 1),
    )


@pytest.fixture
def group_teacher_2(db, course, teacher_profile_2):
    return Group.objects.create(
        name="Group-T2",
        course=course,
        teacher=teacher_profile_2,
        max_students=10,
        start_date=date(2026, 10, 1),
    )


@pytest.fixture
def student_in_group_1(db, group_teacher_1):
    student = Student.objects.create(
        first_name="Talaba",
        last_name="Birinchi",
        phone="+998903333333",
    )
    Enrollment.objects.create(
        student=student,
        group=group_teacher_1,
        status=Enrollment.Status.ACTIVE,
    )
    return student


@pytest.fixture
def student_in_group_2(db, group_teacher_2):
    student = Student.objects.create(
        first_name="Talaba",
        last_name="Ikkinchi",
        phone="+998904444444",
    )
    Enrollment.objects.create(
        student=student,
        group=group_teacher_2,
        status=Enrollment.Status.ACTIVE,
    )
    return student


@pytest.mark.django_db
class TestTeacherRoleAccessRestrictions:
    """O'qituvchi roli uchun cheklovlar va ruxsatlar testi."""

    def test_teacher_group_list_sees_only_own_groups(
        self, client, teacher_user, group_teacher_1, group_teacher_2
    ):
        """O'qituvchi guruhlar ro'yxatida faqat o'ziga biriktirilgan guruhlarni ko'radi."""
        client.force_login(teacher_user)
        response = client.get(reverse("groups:group_list"))

        assert response.status_code == 200
        groups_in_context = list(response.context["groups"])
        assert group_teacher_1 in groups_in_context
        assert group_teacher_2 not in groups_in_context

    def test_teacher_group_detail_own_group_allowed(
        self, client, teacher_user, group_teacher_1
    ):
        """O'qituvchi o'z guruhining kartasini ochishi mumkin (200 OK)."""
        client.force_login(teacher_user)
        response = client.get(
            reverse("groups:group_detail", kwargs={"pk": group_teacher_1.pk})
        )
        assert response.status_code == 200
        assert response.context["group"] == group_teacher_1

    def test_teacher_group_detail_other_group_forbidden(
        self, client, teacher_user, group_teacher_2
    ):
        """O'qituvchi boshqa o'qituvchining guruh kartasini ocholmaydi (403 Forbidden)."""
        client.force_login(teacher_user)
        response = client.get(
            reverse("groups:group_detail", kwargs={"pk": group_teacher_2.pk})
        )
        assert response.status_code == 403

    def test_teacher_group_mutations_forbidden(
        self, client, teacher_user, group_teacher_1, student_in_group_1
    ):
        """O'qituvchi guruh yaratish, tahrirlash, o'quvchi qo'shish/chiqarish huquqiga ega emas (403)."""
        client.force_login(teacher_user)

        assert client.get(reverse("groups:group_create")).status_code == 403
        assert (
            client.get(
                reverse("groups:group_edit", kwargs={"pk": group_teacher_1.pk})
            ).status_code
            == 403
        )
        assert (
            client.post(
                reverse("groups:group_enroll", kwargs={"pk": group_teacher_1.pk}),
                {"student": student_in_group_1.pk},
            ).status_code
            == 403
        )
        assert (
            client.post(
                reverse(
                    "groups:group_remove",
                    kwargs={
                        "pk": group_teacher_1.pk,
                        "student_id": student_in_group_1.pk,
                    },
                )
            ).status_code
            == 403
        )

    def test_teacher_student_list_sees_only_own_students(
        self,
        client,
        teacher_user,
        student_in_group_1,
        student_in_group_2,
    ):
        """O'qituvchi o'quvchilar ro'yxatida faqat o'z guruhlaridagi o'quvchilarni ko'radi."""
        client.force_login(teacher_user)
        response = client.get(reverse("students:student_list"))

        assert response.status_code == 200
        students_in_context = list(response.context["students"])
        assert student_in_group_1 in students_in_context
        assert student_in_group_2 not in students_in_context

    def test_teacher_student_detail_own_student_allowed(
        self, client, teacher_user, student_in_group_1
    ):
        """O'qituvchi o'z guruhidagi o'quvchi kartasini ko'ra oladi (200 OK)."""
        client.force_login(teacher_user)
        response = client.get(
            reverse(
                "students:student_detail",
                kwargs={"pk": student_in_group_1.pk},
            )
        )
        assert response.status_code == 200
        assert response.context["student"] == student_in_group_1

    def test_teacher_student_detail_other_student_forbidden(
        self, client, teacher_user, student_in_group_2
    ):
        """O'qituvchi o'ziga tegishli bo'lmagan o'quvchi kartasiga kirganda 403 oladi."""
        client.force_login(teacher_user)
        response = client.get(
            reverse(
                "students:student_detail",
                kwargs={"pk": student_in_group_2.pk},
            )
        )
        assert response.status_code == 403

    def test_teacher_student_mutations_forbidden(
        self, client, teacher_user, student_in_group_1
    ):
        """O'qituvchi o'quvchi yaratish, tahrirlash, arxivlash va vasiy qo'shish huquqiga ega emas (403)."""
        client.force_login(teacher_user)

        assert client.get(reverse("students:student_create")).status_code == 403
        assert (
            client.get(
                reverse(
                    "students:student_edit",
                    kwargs={"pk": student_in_group_1.pk},
                )
            ).status_code
            == 403
        )
        assert (
            client.post(
                reverse(
                    "students:student_archive",
                    kwargs={"pk": student_in_group_1.pk},
                )
            ).status_code
            == 403
        )
        assert (
            client.post(
                reverse(
                    "students:guardian_add",
                    kwargs={"pk": student_in_group_1.pk},
                ),
                {"full_name": "Ota", "phone": "123"},
            ).status_code
            == 403
        )

    def test_teacher_teachers_and_courses_sections_forbidden(
        self, client, teacher_user, course, teacher_profile_1
    ):
        """O'qituvchi o'qituvchilar va kurslar bo'limiga kira olmaydi (403 Forbidden)."""
        client.force_login(teacher_user)

        # Teachers app
        assert client.get(reverse("teachers:teacher_list")).status_code == 403
        assert client.get(reverse("teachers:teacher_create")).status_code == 403
        assert (
            client.get(
                reverse(
                    "teachers:teacher_edit",
                    kwargs={"pk": teacher_profile_1.pk},
                )
            ).status_code
            == 403
        )

        # Courses app
        assert client.get(reverse("courses:course_list")).status_code == 403
        assert client.get(reverse("courses:course_create")).status_code == 403
        assert (
            client.get(
                reverse("courses:course_edit", kwargs={"pk": course.pk})
            ).status_code
            == 403
        )


@pytest.mark.django_db
class TestDirectorAndAdminFullAccess:
    """Direktor va Administrator uchun to'liq huquqlar testi."""

    def test_admin_has_full_access(
        self,
        client,
        admin_user,
        course,
        teacher_profile_1,
        group_teacher_1,
        student_in_group_1,
    ):
        """Administrator barcha ro'yxat va tafsilot sahifalariga cheklovlarsiz kira oladi."""
        client.force_login(admin_user)

        assert client.get(reverse("students:student_list")).status_code == 200
        assert (
            client.get(
                reverse(
                    "students:student_detail",
                    kwargs={"pk": student_in_group_1.pk},
                )
            ).status_code
            == 200
        )
        assert client.get(reverse("students:student_create")).status_code == 200

        assert client.get(reverse("teachers:teacher_list")).status_code == 200
        assert client.get(reverse("teachers:teacher_create")).status_code == 200

        assert client.get(reverse("courses:course_list")).status_code == 200
        assert client.get(reverse("courses:course_create")).status_code == 200

        assert client.get(reverse("groups:group_list")).status_code == 200
        assert (
            client.get(
                reverse("groups:group_detail", kwargs={"pk": group_teacher_1.pk})
            ).status_code
            == 200
        )
        assert client.get(reverse("groups:group_create")).status_code == 200

    def test_director_has_full_access(
        self,
        client,
        director_user,
        course,
        teacher_profile_1,
        group_teacher_1,
        student_in_group_1,
    ):
        """Direktor barcha ro'yxat va tafsilot sahifalariga to'liq kira oladi."""
        client.force_login(director_user)

        assert client.get(reverse("students:student_list")).status_code == 200
        assert (
            client.get(
                reverse(
                    "students:student_detail",
                    kwargs={"pk": student_in_group_1.pk},
                )
            ).status_code
            == 200
        )
        assert client.get(reverse("groups:group_list")).status_code == 200
        assert (
            client.get(
                reverse("groups:group_detail", kwargs={"pk": group_teacher_1.pk})
            ).status_code
            == 200
        )
        assert client.get(reverse("teachers:teacher_list")).status_code == 200
        assert client.get(reverse("courses:course_list")).status_code == 200
