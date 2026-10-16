"""Schedule models: Room, ScheduleSlot, Lesson."""

from django.db import models


class Room(models.Model):
    """O'quv xonasi (kabinet)."""

    name = models.CharField(max_length=100, unique=True, verbose_name="Xona nomi")
    capacity = models.PositiveSmallIntegerField(
        null=True, blank=True, verbose_name="Sig'imi (o'rinlar soni)"
    )
    note = models.CharField(
        max_length=200, blank=True, default="", verbose_name="Izoh (qavat, jihozlar)"
    )
    is_active = models.BooleanField(default=True, verbose_name="Faol holatda")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Yaratilgan vaqti")

    class Meta:
        verbose_name = "Xona"
        verbose_name_plural = "Xonalar"
        ordering = ["name"]
        constraints = [
            models.CheckConstraint(condition=models.Q(capacity__isnull=True) | models.Q(capacity__gte=1), name="room_capacity_positive"),
        ]

    def __str__(self):
        return self.name


class ScheduleSlot(models.Model):
    """Guruhning haftalik dars jadvali shabloni."""

    WEEKDAY_CHOICES = [
        (0, "Dushanba"),
        (1, "Seshanba"),
        (2, "Chorshanba"),
        (3, "Payshanba"),
        (4, "Juma"),
        (5, "Shanba"),
        (6, "Yakshanba"),
    ]

    group = models.ForeignKey(
        "groups.Group",
        on_delete=models.CASCADE,
        related_name="schedule_slots",
        verbose_name="Guruh",
    )
    weekday = models.PositiveSmallIntegerField(
        choices=WEEKDAY_CHOICES,
        verbose_name="Hafta kuni",
    )
    start_time = models.TimeField(verbose_name="Boshlanish vaqti")
    end_time = models.TimeField(verbose_name="Tugash vaqti")
    room = models.ForeignKey(
        Room,
        on_delete=models.PROTECT,
        related_name="schedule_slots",
        verbose_name="Xona",
    )
    is_active = models.BooleanField(default=True, verbose_name="Faol holatda")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Yaratilgan vaqti")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Tahrirlangan vaqti")

    class Meta:
        verbose_name = "Jadval sloti"
        verbose_name_plural = "Jadval slotlari"
        ordering = ["weekday", "start_time"]
        constraints = [
            models.CheckConstraint(condition=models.Q(end_time__gt=models.F("start_time")), name="slot_end_after_start"),
        ]

    def __str__(self):
        return (
            f"{self.group.name} — {self.get_weekday_display()} "
            f"{self.start_time:%H:%M}–{self.end_time:%H:%M} ({self.room.name})"
        )


class Lesson(models.Model):
    """Aniq sana va vaqtdagi dars."""

    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Rejalashtirilgan"
        HELD = "held", "O'tildi"
        CANCELLED = "cancelled", "Bekor qilindi"

    group = models.ForeignKey(
        "groups.Group",
        on_delete=models.PROTECT,
        related_name="lessons",
        verbose_name="Guruh",
    )
    date = models.DateField(verbose_name="Sana")
    start_time = models.TimeField(verbose_name="Boshlanish vaqti")
    end_time = models.TimeField(verbose_name="Tugash vaqti")
    room = models.ForeignKey(
        Room,
        on_delete=models.PROTECT,
        related_name="lessons",
        verbose_name="Xona",
    )
    # O'qituvchi guruhdan olinadi, lekin almashtirilishi mumkin
    teacher = models.ForeignKey(
        "teachers.Teacher",
        on_delete=models.PROTECT,
        related_name="lessons",
        verbose_name="O'qituvchi",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.SCHEDULED,
        verbose_name="Holati",
    )
    note = models.TextField(blank=True, default="", verbose_name="Izoh")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Yaratilgan vaqti")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Tahrirlangan vaqti")

    class Meta:
        verbose_name = "Dars"
        verbose_name_plural = "Darslar"
        ordering = ["date", "start_time"]
        constraints = [
            models.CheckConstraint(condition=models.Q(end_time__gt=models.F("start_time")), name="lesson_end_after_start"),
            models.UniqueConstraint(
                fields=["group", "date", "start_time"],
                name="unique_group_date_start_time",
            )
        ]
        indexes = [
            models.Index(fields=["date"], name="idx_lesson_date"),
            models.Index(fields=["group", "date"], name="idx_lesson_group_date"),
        ]

    def __str__(self):
        return (
            f"{self.group.name} — {self.date} "
            f"{self.start_time:%H:%M} [{self.get_status_display()}]"
        )
