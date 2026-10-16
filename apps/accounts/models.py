"""Custom User model with role choices."""

from django.contrib.auth.models import AbstractUser
from django.contrib.auth.models import UserManager as BaseUserManager
from django.db import models


class UserManager(BaseUserManager):
    """Foydalanuvchilar menejeri (rollar bilan ishlash)."""

    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        # createsuperuser orqali yaratilgan superuser avtomatik ravishda director rolini oladi
        extra_fields.setdefault("role", User.Role.DIRECTOR)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser is_staff=True bo'lishi shart.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser is_superuser=True bo'lishi shart.")

        return self._create_user(username, email, password, **extra_fields)


class User(AbstractUser):
    """Yurtora CRM tizimidagi foydalanuvchi modeli (RBAC)."""

    class Role(models.TextChoices):
        DIRECTOR = "director", "Direktor"
        ADMINISTRATOR = "administrator", "Administrator"
        TEACHER = "teacher", "O'qituvchi"
        ACCOUNTANT = "accountant", "Buxgalter"

    # Minimal zaruriy huquqlar printsipi bo'yicha default qiymat: TEACHER
    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.TEACHER,
        verbose_name="Roli",
        help_text="CRM tizimidagi foydalanuvchining kirish huquqini belgilaydi",
    )

    objects = UserManager()

    class Meta:
        verbose_name = "Foydalanuvchi"
        verbose_name_plural = "Foydalanuvchilar"

    def __str__(self):
        full_name = self.get_full_name()
        display_name = full_name if full_name else self.username
        return f"{display_name} ({self.get_role_display()})"

    @property
    def is_director(self) -> bool:
        return self.role == self.Role.DIRECTOR

    @property
    def is_administrator(self) -> bool:
        return self.role == self.Role.ADMINISTRATOR

    @property
    def is_teacher(self) -> bool:
        return self.role == self.Role.TEACHER
