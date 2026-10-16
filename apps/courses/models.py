"""Course model."""

from django.db import models


class Course(models.Model):
    """O'quv kursi modeli."""

    name = models.CharField(max_length=200, verbose_name="Kurs nomi")
    level = models.CharField(
        max_length=50,
        blank=True,
        default="",
        verbose_name="Darajasi",  # masalan: Beginner, IELTS
    )
    monthly_price = models.BigIntegerField(
        default=0,
        verbose_name="Oylik to'lov summasi (so'm)",
        help_text="To'lov summasi so'mda butun son sifatida saqlanadi",
    )
    is_active = models.BooleanField(default=True, verbose_name="Faol holatda")

    created_at = models.DateTimeField(
        auto_now_add=True, verbose_name="Yaratilgan vaqti"
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Tahrirlangan vaqti")

    class Meta:
        verbose_name = "Kurs"
        verbose_name_plural = "Kurslar"
        constraints = [
            models.CheckConstraint(condition=models.Q(monthly_price__gte=0), name="course_price_non_negative"),
        ]

    def __str__(self):
        if self.level:
            return f"{self.name} ({self.level})"
        return self.name
