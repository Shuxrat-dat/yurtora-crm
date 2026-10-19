"""Group and Enrollment models."""

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

PERCENT_FROM_GROUP = "group"      # guruhning alohida foizi
PERCENT_FROM_TEACHER = "teacher"  # o'qituvchi profilidagi asosiy foiz


class Group(models.Model):
    """O'quv guruhi modeli."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Faol"
        CLOSED = "closed", "Yopilgan"

    name = models.CharField(max_length=100, verbose_name="Guruh nomi")
    course = models.ForeignKey(
        "courses.Course",
        on_delete=models.PROTECT,
        related_name="groups",
        verbose_name="Kurs",
    )
    teacher = models.ForeignKey(
        "teachers.Teacher",
        on_delete=models.PROTECT,
        related_name="groups",
        verbose_name="O'qituvchi",
    )
    max_students = models.PositiveIntegerField(
        default=15,
        verbose_name="Maksimal o'quvchilar soni",
    )
    # Bo'sh (NULL) = alohida foiz yo'q, o'qituvchi profilidagi asosiy foiz ishlatiladi.
    # 0 — aniq belgilangan guruh foizi (asosiy foiz bilan almashtirilmaydi).
    teacher_percent = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        default=None,
        verbose_name="O'qituvchi foizi (%)",
        help_text="Bo'sh qoldirilsa, o'qituvchi profilidagi asosiy foiz ishlatiladi (0-100)",
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    start_date = models.DateField(verbose_name="Boshlanish sanasi")
    end_date = models.DateField(null=True, blank=True, verbose_name="Tugash sanasi")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        verbose_name="Holati",
    )

    created_at = models.DateTimeField(
        auto_now_add=True, verbose_name="Yaratilgan vaqti"
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Tahrirlangan vaqti")

    class Meta:
        verbose_name = "Guruh"
        verbose_name_plural = "Guruhlar"
        constraints = [
            models.CheckConstraint(condition=models.Q(teacher_percent__lte=100), name="group_teacher_percent_max_100"),
            models.CheckConstraint(condition=models.Q(max_students__gte=1), name="group_max_students_positive"),
            models.CheckConstraint(condition=models.Q(end_date__isnull=True) | models.Q(end_date__gte=models.F("start_date")), name="group_end_not_before_start"),
        ]

    def __str__(self):
        return f"{self.name} ({self.course.name})"

    def resolve_teacher_percent(self, teacher=None) -> tuple[int | None, str | None]:
        """
        Ish haqi foizini aniqlashning yagona qoidasi (barcha hisob-kitob va ekranlar shu yerdan oladi).

        Qaytaradi (foiz, manba):
        - guruhda foiz belgilangan (0 ham) -> (guruh foizi, PERCENT_FROM_GROUP);
        - aks holda o'qituvchi profilidagi asosiy foiz -> (asosiy foiz, PERCENT_FROM_TEACHER);
        - ikkalasi ham yo'q -> (None, None): ish haqi hisoblanmaydi.
        teacher: oldindan olingan o'qituvchi obyekti (qo'shimcha so'rov bo'lmasligi uchun).
        """
        if self.teacher_percent is not None:
            return self.teacher_percent, PERCENT_FROM_GROUP
        teacher = teacher if teacher is not None else self.teacher
        if teacher is not None and teacher.base_percent is not None:
            return teacher.base_percent, PERCENT_FROM_TEACHER
        return None, None

    @property
    def active_students_count(self) -> int:
        return self.enrollments.filter(
            status__in=[
                Enrollment.Status.ACTIVE,
                Enrollment.Status.TRIAL,
                Enrollment.Status.FROZEN,
            ]
        ).count()


class Enrollment(models.Model):
    """O'quvchining guruhga a'zolik yozuvi."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Faol"
        TRIAL = "trial", "Sinov darsi"
        FROZEN = "frozen", "Muzlatilgan"
        FINISHED = "finished", "Tugallangan"

    student = models.ForeignKey(
        "students.Student",
        on_delete=models.PROTECT,
        related_name="enrollments",
        verbose_name="O'quvchi",
    )
    group = models.ForeignKey(
        Group,
        on_delete=models.PROTECT,
        related_name="enrollments",
        verbose_name="Guruh",
    )
    start_date = models.DateField(
        default=timezone.now, verbose_name="Guruhga qo'shilgan sana"
    )
    end_date = models.DateField(
        null=True, blank=True, verbose_name="Guruhdan chiqqan sana"
    )
    # Shaxsiy oylik narx: bo'sh bo'lsa, kursning narxi ishlatiladi (chegirma, erta yozilish, kelishuv)
    custom_price = models.BigIntegerField(null=True, blank=True, verbose_name="Shaxsiy oylik narx (so'm)")
    price_note = models.CharField(max_length=120, blank=True, default="", verbose_name="Narx sababi")
    paid_from = models.DateField(
        null=True, blank=True, verbose_name="To'lov boshlanish sanasi"
    )
    frozen_from = models.DateField(
        null=True, blank=True, verbose_name="Muzlatilgan sana"
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        verbose_name="Holati",
    )

    created_at = models.DateTimeField(
        auto_now_add=True, verbose_name="Yaratilgan vaqti"
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Tahrirlangan vaqti")

    class Meta:
        verbose_name = "Guruhga a'zolik"
        verbose_name_plural = "Guruhga a'zoliklar"
        constraints = [
            models.CheckConstraint(condition=models.Q(end_date__isnull=True) | models.Q(end_date__gte=models.F("start_date")), name="enrollment_end_not_before_start"),
            models.CheckConstraint(condition=models.Q(custom_price__isnull=True) | models.Q(custom_price__gte=0), name="enrollment_custom_price_non_negative"),
            # Bir guruhda bitta o'quvchi uchun faqat bitta tugallanmagan (trial/active/frozen) a'zolik bo'lishi mumkin
            models.UniqueConstraint(
                fields=["student", "group"],
                condition=models.Q(status__in=["trial", "active", "frozen"]),
                name="unique_open_enrollment_per_group",
            ),
        ]

    @property
    def effective_price(self) -> int:
        """Shu o'quvchi uchun amaldagi oylik narx: shaxsiy narx yoki kurs narxi."""
        return self.custom_price if self.custom_price is not None else self.group.course.monthly_price

    def __str__(self):
        return f"{self.student} -> {self.group} ({self.get_status_display()})"


class EnrollmentLog(models.Model):
    """Guruhga a'zolik holati o'zgarishi jurnali."""

    enrollment = models.ForeignKey(
        Enrollment,
        on_delete=models.CASCADE,
        related_name="logs",
        verbose_name="A'zolik",
    )
    action = models.CharField(max_length=50, verbose_name="Harakat")
    old_status = models.CharField(
        max_length=20, null=True, blank=True, verbose_name="Eski holat"
    )
    new_status = models.CharField(max_length=20, verbose_name="Yangi holat")
    changed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="enrollment_logs",
        verbose_name="O'zgartirgan foydalanuvchi",
    )
    changed_at = models.DateTimeField(
        auto_now_add=True, verbose_name="O'zgartirilgan vaqti"
    )
    note = models.TextField(blank=True, default="", verbose_name="Izoh")

    class Meta:
        verbose_name = "A'zolik o'zgarishi jurnali"
        verbose_name_plural = "A'zolik o'zgarishlari jurnali"
        ordering = ["-changed_at"]

    def __str__(self):
        return f"{self.enrollment}: {self.old_status} -> {self.new_status} ({self.action})"
