"""Teacher model."""

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Teacher(models.Model):
    """O'qituvchi modeli."""

    class PayType(models.TextChoices):
        PERCENT = "percent", "Foiz asosida"
        FIXED = "fixed", "Belgilangan oylik"

    full_name = models.CharField(max_length=255, verbose_name="To'liq ismi")
    phone = models.CharField(max_length=20, verbose_name="Telefon raqami")
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="teacher_profile",
        verbose_name="Foydalanuvchi akkaunti",
    )
    pay_type = models.CharField(
        max_length=20,
        choices=PayType.choices,
        default=PayType.PERCENT,
        verbose_name="To'lov turi",
        help_text="Foiz asosida yoki belgilangan oylik",
    )
    monthly_salary = models.BigIntegerField(
        null=True,
        blank=True,
        verbose_name="Oylik maoshi (so'm)",
        help_text="Faqat 'Belgilangan oylik' turi uchun",
    )
    base_percent = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        verbose_name="Asosiy ish haqi foizi (%)",
        help_text="Alohida foiz belgilanmagan barcha guruhlar uchun ishlatiladi (0-100)",
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    is_active = models.BooleanField(default=True, verbose_name="Faol holatda")

    created_at = models.DateTimeField(
        auto_now_add=True, verbose_name="Yaratilgan vaqti"
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Tahrirlangan vaqti")

    class Meta:
        verbose_name = "O'qituvchi"
        verbose_name_plural = "O'qituvchilar"
        constraints = [
            models.CheckConstraint(condition=models.Q(base_percent__lte=100), name="teacher_base_percent_max_100"),
        ]

    def __str__(self):
        return f"{self.full_name} ({self.phone})"

    def clean(self):
        """Validate that fixed pay_type requires monthly_salary."""
        from django.core.exceptions import ValidationError

        if self.pay_type == self.PayType.FIXED and not self.monthly_salary:
            raise ValidationError(
                {"monthly_salary": "Belgilangan oylik turi uchun oylik maoshi majburiy."}
            )
