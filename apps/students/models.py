"""Student, Guardian and StudentGuardian models."""

from django.db import models
from django.utils import timezone


class Student(models.Model):
    """O'quvchi modeli."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Faol"
        ARCHIVED = "archived", "Arxivlangan"
        FROZEN = "frozen", "Muzlatilgan"

    class ArchiveReason(models.TextChoices):
        PRICE = "price", "Narx qimmat"
        SCHEDULE = "schedule", "Jadval mos kelmadi"
        QUALITY = "quality", "Dars sifatidan norozi"
        COMPLETED = "completed", "Kursni tugatdi"
        MOVED = "moved", "Ko'chib ketdi"
        COMPETITOR = "competitor", "Boshqa markazga o'tdi"
        NO_TIME = "no_time", "Vaqti yo'q"
        OTHER = "other", "Boshqa sabab"

    class Gender(models.TextChoices):
        MALE = "male", "Erkak"
        FEMALE = "female", "Ayol"

    class Source(models.TextChoices):
        INSTAGRAM = "instagram", "Instagram"
        TELEGRAM = "telegram", "Telegram"
        WEBSITE = "website", "Veb-sayt"
        REFERRAL = "referral", "Tavsiya (tanishlar)"
        WALK_IN = "walk_in", "O'zi kelgan (tashrif)"
        OTHER = "other", "Boshqa"

    first_name = models.CharField(max_length=100, verbose_name="Ismi")
    last_name = models.CharField(max_length=100, verbose_name="Familiyasi")
    middle_name = models.CharField(
        max_length=100, blank=True, default="", verbose_name="Otasining ismi"
    )
    phone = models.CharField(max_length=20, verbose_name="Telefon raqami")
    birth_date = models.DateField(
        null=True, blank=True, verbose_name="Tug'ilgan sanasi"
    )
    gender = models.CharField(
        max_length=10,
        choices=Gender.choices,
        blank=True,
        default="",
        verbose_name="Jinsi",
    )
    school = models.CharField(
        max_length=150,
        blank=True,
        default="",
        verbose_name="Maktabi / O'qish joyi",
    )
    source = models.CharField(
        max_length=20,
        choices=Source.choices,
        blank=True,
        default="",
        verbose_name="Qayerdan bildi (manba)",
    )
    address = models.CharField(
        max_length=255, blank=True, default="", verbose_name="Manzili"
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        verbose_name="Holati",
    )
    archive_reason = models.CharField(
        max_length=20,
        choices=ArchiveReason.choices,
        blank=True,
        default="",
        verbose_name="Arxivlash sababi",
    )
    archived_at = models.DateTimeField(
        null=True, blank=True, verbose_name="Arxivlangan vaqti"
    )
    is_blacklisted = models.BooleanField(
        default=False,
        verbose_name="Qora ro'yxatda",
    )
    blacklist_reason = models.TextField(
        blank=True,
        default="",
        verbose_name="Qora ro'yxatga kiritish sababi",
    )
    joined_date = models.DateField(
        default=timezone.now, verbose_name="Markazga kelgan sana"
    )
    notes = models.TextField(blank=True, default="", verbose_name="Eslatmalar")

    created_at = models.DateTimeField(
        auto_now_add=True, verbose_name="Yaratilgan vaqti"
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Tahrirlangan vaqti")

    class Meta:
        verbose_name = "O'quvchi"
        verbose_name_plural = "O'quvchilar"
        indexes = [
            models.Index(fields=["phone"], name="idx_student_phone"),
            models.Index(fields=["last_name"], name="idx_student_last_name"),
            models.Index(fields=["status"], name="idx_student_status"),
        ]

    def __str__(self):
        return f"{self.first_name} {self.last_name}"

    def change_status(self, new_status, reason="", user=None):
        """Holatni o'zgartiradi; arxivlashda sabab va vaqtni yozadi, qaytarishda tozalaydi."""
        self.status = new_status
        if new_status == self.Status.ARCHIVED:
            self.archive_reason = reason if reason in self.ArchiveReason.values else ""
            self.archived_at = timezone.now()
        else:
            self.archive_reason = ""
            self.archived_at = None
        self.save(
            update_fields=["status", "archive_reason", "archived_at", "updated_at"]
        )
        if new_status == self.Status.ARCHIVED:
            # Arxivlangan o'quvchi guruhlarda qolmasligi va hisob-kitob davom etmasligi uchun
            from apps.groups.services import finish_open_enrollments

            finish_open_enrollments(student=self, user=user, note="O'quvchi arxivlandi")

    @property
    def full_name(self):
        parts = [self.last_name, self.first_name, self.middle_name]
        return " ".join(p for p in parts if p).strip()


class Guardian(models.Model):
    """O'quvchining vasiysi yoki ota-onasi."""

    full_name = models.CharField(max_length=255, verbose_name="To'liq ismi")
    phone = models.CharField(max_length=20, verbose_name="Telefon raqami")
    notes = models.TextField(blank=True, default="", verbose_name="Eslatmalar")
    created_at = models.DateTimeField(
        auto_now_add=True, verbose_name="Yaratilgan vaqti"
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Tahrirlangan vaqti")

    class Meta:
        verbose_name = "Vasiy"
        verbose_name_plural = "Vasiylar"

    def __str__(self):
        return f"{self.full_name} ({self.phone})"


class StudentGuardian(models.Model):
    """O'quvchi va uning vasiysi o'rtasidagi bog'lanish."""

    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="student_guardians",
        verbose_name="O'quvchi",
    )
    guardian = models.ForeignKey(
        Guardian,
        on_delete=models.CASCADE,
        related_name="student_guardians",
        verbose_name="Vasiy",
    )
    relation = models.CharField(
        max_length=50,
        blank=True,
        default="",
        verbose_name="Qarindoshlik darajasi",  # masalan: Otasi, Onasi
    )
    is_primary = models.BooleanField(default=False, verbose_name="Asosiy aloqa shaxsi")

    class Meta:
        verbose_name = "O'quvchi vasiysi"
        verbose_name_plural = "O'quvchi vasiylari"
        constraints = [
            models.UniqueConstraint(
                fields=["student", "guardian"],
                name="unique_student_guardian",
            )
        ]

    def __str__(self):
        return f"{self.student} - {self.guardian} ({self.relation})"
