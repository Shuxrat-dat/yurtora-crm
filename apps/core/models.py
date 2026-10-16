from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    """Xodimlar harakatlari jurnali: kim, qachon, nimani yaratdi/o'zgartirdi/o'chirdi."""

    class Action(models.TextChoices):
        CREATE = "create", "Yaratildi"
        UPDATE = "update", "O'zgartirildi"
        DELETE = "delete", "O'chirildi"
        LOGIN = "login", "Tizimga kirdi"
        LOGOUT = "logout", "Tizimdan chiqdi"
        LOGIN_FAILED = "login_failed", "Kirish muvaffaqiyatsiz"

    created_at = models.DateTimeField("Vaqt", auto_now_add=True, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="Foydalanuvchi",
    )
    username = models.CharField("Login", max_length=150, blank=True)
    action = models.CharField("Harakat", max_length=16, choices=Action.choices)
    model = models.CharField("Bo'lim", max_length=64, blank=True)
    object_id = models.CharField("Obyekt ID", max_length=32, blank=True)
    object_repr = models.CharField("Obyekt", max_length=255, blank=True)
    changes = models.JSONField("O'zgarishlar", default=dict, blank=True)
    ip_address = models.GenericIPAddressField("IP", null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "Harakat jurnali"
        verbose_name_plural = "Harakatlar jurnali"
        indexes = [models.Index(fields=["model", "object_id"])]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.username} {self.action} {self.model}"

    # Jurnal yozuvlari o'zgartirilmaydi
    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError("AuditLog yozuvini o'zgartirib bo'lmaydi.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("AuditLog yozuvini o'chirib bo'lmaydi.")
