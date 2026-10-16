"""Attendance models: Attendance, AttendanceLog."""

from django.conf import settings
from django.db import models


class Attendance(models.Model):
    """O'quvchining dars davomati yozuvi."""

    class Status(models.TextChoices):
        PRESENT = "present", "Keldi"
        ABSENT = "absent", "Kelmadi"
        EXCUSED = "excused", "Sababli kelmadi"
        LATE = "late", "Kechikib keldi"

    lesson = models.ForeignKey(
        "schedule.Lesson",
        on_delete=models.CASCADE,
        related_name="attendances",
        verbose_name="Dars",
    )
    student = models.ForeignKey(
        "students.Student",
        on_delete=models.CASCADE,
        related_name="attendances",
        verbose_name="O'quvchi",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        verbose_name="Holati",
    )
    marked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="marked_attendances",
        verbose_name="Kim tomonidan belgilandi",
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Tahrirlangan vaqti")

    class Meta:
        verbose_name = "Davomat"
        verbose_name_plural = "Davomatlar"
        constraints = [
            models.UniqueConstraint(
                fields=["lesson", "student"],
                name="unique_lesson_student_attendance",
            )
        ]
        indexes = [
            models.Index(fields=["lesson"], name="idx_attendance_lesson"),
            models.Index(fields=["student"], name="idx_attendance_student"),
        ]

    def __str__(self):
        return (
            f"{self.student} — {self.lesson.date} [{self.get_status_display()}]"
        )


class AttendanceLog(models.Model):
    """Davomat o'zgarishlari jurnali."""

    attendance = models.ForeignKey(
        Attendance,
        on_delete=models.CASCADE,
        related_name="logs",
        verbose_name="Davomat yozuvi",
    )
    old_status = models.CharField(
        max_length=20,
        blank=True,
        default="",
        verbose_name="Oldingi holat",
    )
    new_status = models.CharField(
        max_length=20,
        verbose_name="Yangi holat",
    )
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="attendance_log_changes",
        verbose_name="O'zgartirgan foydalanuvchi",
    )
    changed_at = models.DateTimeField(auto_now_add=True, verbose_name="O'zgartirilgan vaqti")

    class Meta:
        verbose_name = "Davomat jurnali"
        verbose_name_plural = "Davomat jurnali"
        ordering = ["-changed_at"]

    def __str__(self):
        return (
            f"{self.attendance} {self.old_status}→{self.new_status} "
            f"({self.changed_at:%Y-%m-%d %H:%M})"
        )
