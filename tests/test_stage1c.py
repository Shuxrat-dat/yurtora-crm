"""Tests for Stage 1C: Trial status, student source, school, and blacklist.

Scenarios tested:
1. Создание ученика без источника и школы.
2. Сохранение и отображение источника и школы.
3. Фильтрация учеников по источнику и школе.
4. Фильтрация по преподавателю без дублирования учеников.
5. Фильтрация черного списка.
6. Преобразование trial в active.
7. Учет пробных записей в лимите группы.
8. Запрет некорректных переходов статусов.
9. Проверка прав доступа к изменению черного списка.
10. Сохранение существующих сценариев создания и редактирования учеников.
"""

from datetime import date

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from apps.accounts.models import User
from apps.courses.models import Course
from apps.groups.models import Enrollment, Group
from apps.groups.services import (
    convert_trial_to_active,
    enroll_student,
    freeze_enrollment,
    remove_student,
    unfreeze_enrollment,
)
from apps.students.forms import StudentForm
from apps.students.models import Student
from apps.teachers.models import Teacher


@pytest.fixture
def course(db):
    return Course.objects.create(
        name="Matematika",
        level="Asosiy",
        monthly_price=500000,
    )


@pytest.fixture
def teacher_1(db):
    user = User.objects.create_user(
        username="t1_user", password="pwd", role=User.Role.TEACHER
    )
    return Teacher.objects.create(
        full_name="Olim Karimov",
        phone="+998901110001",
        user=user,
        is_active=True,
    )


@pytest.fixture
def teacher_2(db):
    user = User.objects.create_user(
        username="t2_user", password="pwd", role=User.Role.TEACHER
    )
    return Teacher.objects.create(
        full_name="Nodira Karimova",
        phone="+998901110002",
        user=user,
        is_active=True,
    )


@pytest.fixture
def group_a(db, course, teacher_1):
    return Group.objects.create(
        name="MATH-01",
        course=course,
        teacher=teacher_1,
        max_students=3,
        start_date=date(2026, 10, 1),
        status=Group.Status.ACTIVE,
    )


@pytest.fixture
def group_b(db, course, teacher_1):
    return Group.objects.create(
        name="MATH-02",
        course=course,
        teacher=teacher_1,
        max_students=5,
        start_date=date(2026, 10, 1),
        status=Group.Status.ACTIVE,
    )


@pytest.mark.django_db
class TestStage1C:
    """Stage 1C test to'plami."""

    # 1. Создание ученика без источника и школы
    def test_create_student_without_source_and_school(self):
        student = Student.objects.create(
            first_name="Bobur",
            last_name="Mirzayev",
            phone="+998901112233",
        )
        assert student.pk is not None
        assert student.source == ""
        assert student.school == ""
        assert student.is_blacklisted is False
        assert student.blacklist_reason == ""

    # 2. Сохранение и отображение источника и школы
    def test_save_and_display_source_and_school(self, client, admin_user):
        client.force_login(admin_user)
        student = Student.objects.create(
            first_name="Shahzod",
            last_name="Yuldashev",
            phone="+998902223344",
            source=Student.Source.TELEGRAM,
            school="21-maktab",
        )
        assert student.get_source_display() == "Telegram"
        assert student.school == "21-maktab"

        # Kartochkada ko'rinishi
        url = reverse("students:student_detail", kwargs={"pk": student.pk})
        response = client.get(url)
        assert response.status_code == 200
        assert "21-maktab" in response.content.decode("utf-8")
        assert "Telegram" in response.content.decode("utf-8")

    # 3. Фильтрация учеников по источнику и школе
    def test_filter_students_by_source_and_school(self, client, admin_user):
        client.force_login(admin_user)
        s1 = Student.objects.create(
            first_name="Anvar",
            last_name="Alimov",
            phone="+998903330001",
            source=Student.Source.INSTAGRAM,
            school="5-litsey",
        )
        s2 = Student.objects.create(
            first_name="Bekzod",
            last_name="Botirov",
            phone="+998903330002",
            source=Student.Source.TELEGRAM,
            school="5-litsey",
        )
        s3 = Student.objects.create(
            first_name="Charos",
            last_name="Chorieva",
            phone="+998903330003",
            source=Student.Source.INSTAGRAM,
            school="17-maktab",
        )

        url = reverse("students:student_list")

        # Filtr: Faqat Instagram
        res_source = client.get(url, {"source": "instagram"})
        assert res_source.status_code == 200
        students_source = res_source.context["students"]
        assert s1 in students_source
        assert s3 in students_source
        assert s2 not in students_source

        # Filtr: Faqat 5-litsey
        res_school = client.get(url, {"school": "litsey"})
        assert res_school.status_code == 200
        students_school = res_school.context["students"]
        assert s1 in students_school
        assert s2 in students_school
        assert s3 not in students_school

        # Birgalikdagi filtr: Instagram + 5-litsey
        res_both = client.get(url, {"source": "instagram", "school": "5-litsey"})
        assert res_both.status_code == 200
        students_both = res_both.context["students"]
        assert s1 in students_both
        assert s2 not in students_both
        assert s3 not in students_both

    # 4. Фильтрация по преподавателю без дублирования учеников
    def test_filter_by_teacher_without_duplicates(
        self, client, admin_user, teacher_1, teacher_2, group_a, group_b
    ):
        client.force_login(admin_user)
        student = Student.objects.create(
            first_name="Davron",
            last_name="Rustamov",
            phone="+998904445566",
        )
        # O'quvchi bitta o'qituvchining IKKITA guruhiga a'zo
        enroll_student(student=student, group=group_a)
        enroll_student(student=student, group=group_b)

        # Boshqa o'quvchi ikkinchi o'qituvchining guruhida
        other_group = Group.objects.create(
            name="MATH-OTHER",
            course=group_a.course,
            teacher=teacher_2,
            max_students=10,
            start_date=date(2026, 10, 1),
        )
        student_other = Student.objects.create(
            first_name="Erkin",
            last_name="Ergashev",
            phone="+998905556677",
        )
        enroll_student(student=student_other, group=other_group)

        url = reverse("students:student_list")
        res = client.get(url, {"teacher": teacher_1.id})
        assert res.status_code == 200

        students_found = list(res.context["students"])
        # Davron faqat BIR MARTA bo'lishi kerak (dubl bo'lmasligi shart)
        assert students_found.count(student) == 1
        assert student in students_found
        assert student_other not in students_found

    # 5. Фильтрация черного списка
    def test_blacklist_filter_and_display(self, client, admin_user):
        client.force_login(admin_user)
        good_student = Student.objects.create(
            first_name="Farrux",
            last_name="Fayzullayev",
            phone="+998906660001",
            is_blacklisted=False,
        )
        bad_student = Student.objects.create(
            first_name="G'olib",
            last_name="G'aniyev",
            phone="+998906660002",
            is_blacklisted=True,
            blacklist_reason="Darslarni muntazam buzgan",
        )

        url = reverse("students:student_list")

        # Faqat qora ro'yxatdagilar
        res_blacklisted = client.get(url, {"blacklist": "yes"})
        assert res_blacklisted.status_code == 200
        assert bad_student in res_blacklisted.context["students"]
        assert good_student not in res_blacklisted.context["students"]

        # Qora ro'yxatda bo'lmaganlar
        res_clean = client.get(url, {"blacklist": "no"})
        assert res_clean.status_code == 200
        assert good_student in res_clean.context["students"]
        assert bad_student not in res_clean.context["students"]

        # Kartochkada ogohlantirish va sabab ko'rinishi
        detail_url = reverse("students:student_detail", kwargs={"pk": bad_student.pk})
        res_detail = client.get(detail_url)
        assert res_detail.status_code == 200
        content = res_detail.content.decode("utf-8")
        assert "Qora ro&#x27;yxat" in content or "Qora ro'yxat" in content
        assert "Darslarni muntazam buzgan" in content

    # 6. Преобразование trial в active
    def test_convert_trial_to_active(self, group_a):
        student = Student.objects.create(
            first_name="Hilola",
            last_name="Hamidova",
            phone="+998907770001",
        )
        enrollment = enroll_student(
            student=student,
            group=group_a,
            status=Enrollment.Status.TRIAL,
        )
        assert enrollment.status == Enrollment.Status.TRIAL

        # Trial dan active ga o'tkazish
        updated = convert_trial_to_active(enrollment)
        assert updated.status == Enrollment.Status.ACTIVE

        enrollment.refresh_from_db()
        assert enrollment.status == Enrollment.Status.ACTIVE

    # 7. Учет пробных записей в лимите группы
    def test_trial_student_counts_in_group_max_students(self, group_a):
        # group_a max_students = 3
        s1 = Student.objects.create(first_name="S1", last_name="L1", phone="90001")
        s2 = Student.objects.create(first_name="S2", last_name="L2", phone="90002")
        s3 = Student.objects.create(first_name="S3", last_name="L3", phone="90003")
        s4 = Student.objects.create(first_name="S4", last_name="L4", phone="90004")

        # 2 ta faol + 1 ta sinov = 3 ta
        enroll_student(s1, group_a, status=Enrollment.Status.ACTIVE)
        enroll_student(s2, group_a, status=Enrollment.Status.ACTIVE)
        enroll_student(s3, group_a, status=Enrollment.Status.TRIAL)

        assert group_a.active_students_count == 3

        # 4-o'quvchini qo'shishda limit xatoligi chiqishi shart (faol bo'lsa ham, trial bo'lsa ham)
        with pytest.raises(ValidationError) as exc_info:
            enroll_student(s4, group_a, status=Enrollment.Status.ACTIVE)
        assert "to'lgan" in str(exc_info.value)

        with pytest.raises(ValidationError) as exc_info_trial:
            enroll_student(s4, group_a, status=Enrollment.Status.TRIAL)
        assert "to'lgan" in str(exc_info_trial.value)

    # 8. Запрет некорректных переходов статусов
    def test_invalid_status_transitions_prevented(self, group_a):
        student = Student.objects.create(first_name="Javohir", last_name="Jalilov", phone="90005")
        active_enrollment = enroll_student(student, group_a, status=Enrollment.Status.ACTIVE)

        # 1) Faol bo'lgan a'zolikni convert_trial_to_active ga chaqirib bo'lmaydi
        with pytest.raises(ValidationError) as exc:
            convert_trial_to_active(active_enrollment)
        assert "Faqat sinov darsidagi" in str(exc.value)

        # 2) Chiqarilgan (finished) a'zolikni convert_trial_to_active ga chaqirib bo'lmaydi
        remove_student(student, group_a)
        active_enrollment.refresh_from_db()
        assert active_enrollment.status == Enrollment.Status.FINISHED

        with pytest.raises(ValidationError) as exc_finished:
            convert_trial_to_active(active_enrollment)
        assert "Faqat sinov darsidagi" in str(exc_finished.value)

        # 3) Yopilgan guruhdagi trial ni active qilib bo'lmaydi
        s_trial = Student.objects.create(first_name="Kamola", last_name="Karimova", phone="90006")
        trial_enrollment = enroll_student(s_trial, group_a, status=Enrollment.Status.TRIAL)

        group_a.status = Group.Status.CLOSED
        group_a.save()

        with pytest.raises(ValidationError) as exc_closed:
            convert_trial_to_active(trial_enrollment)
        assert "Yopilgan guruh" in str(exc_closed.value)

    # 9. Проверка прав доступа к изменению черного списка
    def test_blacklist_permissions_enforced_in_form(self, admin_user, director_user, teacher_user):
        student = Student.objects.create(
            first_name="Lobar",
            last_name="Latipova",
            phone="+998909990001",
            is_blacklisted=False,
        )

        data = {
            "first_name": "Lobar",
            "last_name": "Latipova",
            "phone": "+998909990001",
            "status": "active",
            "joined_date": "2026-10-01",
            "is_blacklisted": True,
            "blacklist_reason": "To'lov qilmagan",
        }

        # Administrator va Direktor qora ro'yxatga kirita oladi
        form_admin = StudentForm(data=data, instance=student, user=admin_user)
        assert "is_blacklisted" in form_admin.fields
        assert "blacklist_reason" in form_admin.fields
        assert form_admin.is_valid()
        saved_admin = form_admin.save()
        assert saved_admin.is_blacklisted is True
        assert saved_admin.blacklist_reason == "To'lov qilmagan"

        # Oddiy o'qituvchi yoki vakolatsiz foydalanuvchi qora ro'yxatni o'zgartira olmaydi
        saved_admin.is_blacklisted = False
        saved_admin.blacklist_reason = ""
        saved_admin.save()

        # Teacher formasi
        form_teacher = StudentForm(data=data, instance=saved_admin, user=teacher_user)
        assert form_teacher.fields["is_blacklisted"].disabled is True
        assert form_teacher.fields["blacklist_reason"].disabled is True
        assert form_teacher.is_valid()
        saved_teacher = form_teacher.save()
        # Backend to'g'ridan-to'g'ri POST orqali yuborilgan qiymatni e'tiborsiz qoldirishi shart
        assert saved_teacher.is_blacklisted is False
        assert saved_teacher.blacklist_reason == ""

    # 10. Сохранение существующих сценариев создания и редактирования учеников
    def test_existing_student_create_and_update_flows_preserved(self, client, admin_user):
        client.force_login(admin_user)

        create_data = {
            "first_name": "Mansur",
            "last_name": "Mahmudov",
            "phone": "+998908889900",
            "status": "active",
            "joined_date": "2026-10-01",
            "school": "14-sonli maktab",
            "source": "walk_in",
            "notes": "Muntazam qatnashmoqchi",
        }

        res_create = client.post(reverse("students:student_create"), create_data)
        assert res_create.status_code == 302

        created_student = Student.objects.get(phone="+998908889900")
        assert created_student.first_name == "Mansur"
        assert created_student.school == "14-sonli maktab"
        assert created_student.source == "walk_in"

        # Tahrirlash
        update_data = {
            "first_name": "Mansur",
            "last_name": "Mahmudov (Tahrir)",
            "phone": "+998908889900",
            "status": "active",
            "joined_date": "2026-10-01",
            "school": "Prezident maktabi",
            "source": "referral",
            "notes": "Yangilangan eslatma",
        }
        res_update = client.post(
            reverse("students:student_edit", kwargs={"pk": created_student.pk}),
            update_data,
        )
        assert res_update.status_code == 302

        created_student.refresh_from_db()
        assert created_student.last_name == "Mahmudov (Tahrir)"
        assert created_student.school == "Prezident maktabi"
        assert created_student.source == "referral"
        assert created_student.notes == "Yangilangan eslatma"

    # 11. Заморозка и разморозка (freeze / unfreeze)
    def test_freeze_and_unfreeze_enrollment(self, group_a, admin_user):
        student = Student.objects.create(
            first_name="Zafar",
            last_name="Zokirov",
            phone="+998909998877",
        )
        enrollment = enroll_student(
            student=student,
            group=group_a,
            status=Enrollment.Status.ACTIVE,
            user=admin_user,
        )
        assert enrollment.status == Enrollment.Status.ACTIVE
        assert enrollment.frozen_from is None

        # Muzlatish
        frozen = freeze_enrollment(enrollment, user=admin_user, note="Vaqtincha dam oladi")
        assert frozen.status == Enrollment.Status.FROZEN
        assert frozen.frozen_from == date.today()

        # Muzlatilgan o'quvchi guruhda o'rin band qilishda davom etadi
        assert group_a.active_students_count == 1

        # Faol bo'lmagan (allaqachon muzlatilgan) a'zolikni qayta muzlatib bo'lmaydi
        with pytest.raises(ValidationError):
            freeze_enrollment(frozen, user=admin_user)

        # Muzlatishni bekor qilish (unfreeze)
        resumed_date = date(2026, 10, 15)
        unfrozen = unfreeze_enrollment(
            frozen, user=admin_user, date=resumed_date, note="Qaytdi"
        )
        assert unfrozen.status == Enrollment.Status.ACTIVE
        assert unfrozen.frozen_from is None
        assert unfrozen.paid_from == resumed_date

        # Faol a'zolikni unfreeze qilib bo'lmaydi
        with pytest.raises(ValidationError):
            unfreeze_enrollment(unfrozen, user=admin_user)

    # 12. Teacher не может заморозить ученика чужой группы (403)
    def test_teacher_cannot_freeze_student_of_another_group_403(
        self, client, teacher_1, teacher_2, group_a, course
    ):
        # group_a teacher_1 ga tegishli
        student_a = Student.objects.create(
            first_name="Ali", last_name="Valiyev", phone="+998901112244"
        )
        enrollment_a = enroll_student(student=student_a, group=group_a)

        # teacher_2 ning o'z guruhi
        group_other = Group.objects.create(
            name="MATH-TEACHER2",
            course=course,
            teacher=teacher_2,
            max_students=5,
            start_date=date(2026, 10, 1),
            status=Group.Status.ACTIVE,
        )
        student_other = Student.objects.create(
            first_name="Botir", last_name="Botirov", phone="+998901112255"
        )
        enrollment_other = enroll_student(student=student_other, group=group_other)

        # teacher_1 tizimga kiradi
        client.force_login(teacher_1.user)

        # 1) teacher_1 o'z guruhidagi o'quvchini muzlata oladi (302 redirect)
        res_own = client.post(
            reverse(
                "groups:group_freeze",
                kwargs={"pk": group_a.pk, "enrollment_id": enrollment_a.pk},
            )
        )
        assert res_own.status_code == 302
        enrollment_a.refresh_from_db()
        assert enrollment_a.status == Enrollment.Status.FROZEN

        # 2) teacher_1 begona guruhdagi o'quvchini muzlata olmaydi -> 403 Forbidden!
        res_forbidden = client.post(
            reverse(
                "groups:group_freeze",
                kwargs={"pk": group_other.pk, "enrollment_id": enrollment_other.pk},
            )
        )
        assert res_forbidden.status_code == 403

        # Servis orqali ham PermissionDenied chiqishi shart
        with pytest.raises(PermissionDenied):
            freeze_enrollment(enrollment_other, user=teacher_1.user)

    # 13. Teacher НЕ может размораживать (unfreeze: только administrator и director)
    def test_teacher_cannot_unfreeze_enrollment(
        self, client, teacher_1, admin_user, group_a
    ):
        student = Student.objects.create(
            first_name="Frozen", last_name="Student", phone="+998901119988"
        )
        enrollment = enroll_student(student=student, group=group_a)

        # Сначала админ замораживает
        freeze_enrollment(enrollment, user=admin_user)
        enrollment.refresh_from_db()
        assert enrollment.status == Enrollment.Status.FROZEN

        # Teacher пытается разморозить через view -> 403
        client.force_login(teacher_1.user)
        res = client.post(
            reverse(
                "groups:group_unfreeze",
                kwargs={"pk": group_a.pk, "enrollment_id": enrollment.pk},
            )
        )
        assert res.status_code == 403

        # Servis orqali ham PermissionDenied
        with pytest.raises(PermissionDenied):
            unfreeze_enrollment(enrollment, user=teacher_1.user)

        # Админ может разморозить
        unfrozen = unfreeze_enrollment(enrollment, user=admin_user)
        assert unfrozen.status == Enrollment.Status.ACTIVE

    # 14. Teacher не может перевести с пробного (convert_trial_to_active: administrator и director)
    def test_teacher_cannot_convert_trial_to_active(
        self, client, teacher_1, admin_user, group_a
    ):
        student = Student.objects.create(
            first_name="Sardor", last_name="Sobirov", phone="+998903332211"
        )
        trial_enr = enroll_student(
            student=student, group=group_a, status=Enrollment.Status.TRIAL
        )

        # O'qituvchi urinadi -> 403
        client.force_login(teacher_1.user)
        res = client.post(
            reverse(
                "groups:group_trial_convert",
                kwargs={"pk": group_a.pk, "enrollment_id": trial_enr.pk},
            )
        )
        assert res.status_code == 403

        with pytest.raises(PermissionDenied):
            convert_trial_to_active(trial_enr, user=teacher_1.user)

        # Administrator o'tkaza oladi
        client.force_login(admin_user)
        res_admin = client.post(
            reverse(
                "groups:group_trial_convert",
                kwargs={"pk": group_a.pk, "enrollment_id": trial_enr.pk},
            ),
            {"paid_from": "2026-10-10"},
        )
        assert res_admin.status_code == 302
        trial_enr.refresh_from_db()
        assert trial_enr.status == Enrollment.Status.ACTIVE
        assert trial_enr.paid_from == date(2026, 10, 10)

    # 14. EnrollmentLog пишется при каждом изменении статуса
    def test_enrollment_log_written_on_all_status_changes(self, group_a, admin_user):
        student = Student.objects.create(
            first_name="Madina", last_name="Murodova", phone="+998907776655"
        )

        # 1) Enroll
        enr = enroll_student(
            student=student,
            group=group_a,
            status=Enrollment.Status.TRIAL,
            user=admin_user,
        )
        assert enr.logs.count() == 1
        log1 = enr.logs.first()
        assert log1.action == "enroll"
        assert log1.old_status is None
        assert log1.new_status == Enrollment.Status.TRIAL
        assert log1.changed_by == admin_user

        # 2) Convert trial to active
        convert_trial_to_active(enr, user=admin_user)
        assert enr.logs.count() == 2
        log2 = enr.logs.first()  # ordered by -changed_at
        assert log2.action == "convert_trial"
        assert log2.old_status == Enrollment.Status.TRIAL
        assert log2.new_status == Enrollment.Status.ACTIVE

        # 3) Freeze
        freeze_enrollment(enr, user=admin_user)
        assert enr.logs.count() == 3
        log3 = enr.logs.first()
        assert log3.action == "freeze"
        assert log3.old_status == Enrollment.Status.ACTIVE
        assert log3.new_status == Enrollment.Status.FROZEN

        # 4) Unfreeze
        unfreeze_enrollment(enr, user=admin_user)
        assert enr.logs.count() == 4
        log4 = enr.logs.first()
        assert log4.action == "unfreeze"
        assert log4.old_status == Enrollment.Status.FROZEN
        assert log4.new_status == Enrollment.Status.ACTIVE

        # 5) Remove
        remove_student(student=student, group=group_a, user=admin_user)
        assert enr.logs.count() == 5
        log5 = enr.logs.first()
        assert log5.action == "remove"
        assert log5.old_status == Enrollment.Status.ACTIVE
        assert log5.new_status == Enrollment.Status.FINISHED

    # 15. Уникальность записи: одна незавершённая запись (trial/active/frozen) на пару ученик + группа
    def test_unique_open_enrollment_per_group(self, group_a):
        student = Student.objects.create(
            first_name="Jasur", last_name="Jumayev", phone="+998904443322"
        )
        enr = enroll_student(student=student, group=group_a, status=Enrollment.Status.ACTIVE)

        # Bir vaqtda ikkinchi yozuv qo'shish xatolik beradi
        with pytest.raises(ValidationError):
            enroll_student(student=student, group=group_a, status=Enrollment.Status.TRIAL)

        # Muzlatilganda ham ochiq a'zolik hisoblanadi
        freeze_enrollment(enr)
        with pytest.raises(ValidationError):
            enroll_student(student=student, group=group_a, status=Enrollment.Status.ACTIVE)

        # Tugatilgandan (finished) so'ng yangi a'zolik qo'shish mumkin
        remove_student(student=student, group=group_a)
        new_enr = enroll_student(student=student, group=group_a, status=Enrollment.Status.ACTIVE)
        assert new_enr.status == Enrollment.Status.ACTIVE
