from django.conf import settings
from django.db import models


class TelegramContact(models.Model):
    """O'quvchiga bog'langan Telegram chat (ota-ona yoki o'quvchining o'zi)."""

    student = models.ForeignKey(
        "students.Student",
        on_delete=models.CASCADE,
        related_name="telegram_contacts",
        verbose_name="O'quvchi",
    )
    chat_id = models.BigIntegerField("Telegram chat ID")
    username = models.CharField("Telegram username", max_length=64, blank=True)
    is_active = models.BooleanField("Faol", default=True)
    created_at = models.DateTimeField("Ulangan vaqti", auto_now_add=True)

    class Meta:
        verbose_name = "Telegram aloqa"
        verbose_name_plural = "Telegram aloqalar"
        constraints = [
            models.UniqueConstraint(
                fields=["student", "chat_id"], name="unique_student_telegram_chat"
            )
        ]

    def __str__(self):
        return f"{self.student} ← {self.chat_id}"


class TelegramLinkToken(models.Model):
    """Botga ulanish uchun bir martalik havola tokeni."""

    student = models.ForeignKey(
        "students.Student",
        on_delete=models.CASCADE,
        related_name="telegram_tokens",
    )
    token = models.CharField(max_length=64, unique=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Telegram havola tokeni"
        verbose_name_plural = "Telegram havola tokenlari"


class NotificationLog(models.Model):
    """Yuborilgan (yoki yuborilmagan) bildirishnomalar tarixi."""

    class Kind(models.TextChoices):
        DEBT = "debt", "Qarz eslatmasi"

    class Status(models.TextChoices):
        PENDING = "pending", "Navbatda"
        SENT = "sent", "Yuborildi"
        FAILED = "failed", "Xato"

    student = models.ForeignKey(
        "students.Student",
        on_delete=models.SET_NULL,
        null=True,
        related_name="notification_logs",
    )
    chat_id = models.BigIntegerField()
    kind = models.CharField(max_length=16, choices=Kind.choices)
    text = models.TextField()
    status = models.CharField(max_length=8, choices=Status.choices)
    error = models.CharField(max_length=255, blank=True)
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "Bildirishnoma jurnali"
        verbose_name_plural = "Bildirishnomalar jurnali"
        indexes = [models.Index(fields=["student", "kind", "status", "created_at"])]
        constraints = [
            # Bir chatga bir o'quvchi uchun navbatda faqat bitta xabar: qo'sh bosish dublikat yaratmaydi
            models.UniqueConstraint(
                fields=["student", "chat_id", "kind"],
                condition=models.Q(status="pending"),
                name="unique_pending_notification",
            )
        ]


class AdminChat(models.Model):
    """Xodim (direktor/administrator) Telegram chati: haftalik qarz ro'yxati shu yerga keladi."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="telegram_admin_chats",
    )
    chat_id = models.BigIntegerField(unique=True)
    username = models.CharField(max_length=64, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Xodim Telegram chati"
        verbose_name_plural = "Xodim Telegram chatlari"


class AdminChatToken(models.Model):
    """Xodim chatini ulash uchun bir martalik token."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+"
    )
    token = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)


class DebtDigest(models.Model):
    """Haftalik qarz ro'yxati: xodim tasdiqlagandan keyingina ota-onalarga yuboriladi."""

    class Status(models.TextChoices):
        PENDING = "pending", "Tasdiq kutilmoqda"
        SENT = "sent", "Yuborildi"
        CANCELLED = "cancelled", "Bekor qilindi"
        EXPIRED = "expired", "Muddati tugadi"

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    student_ids = models.JSONField(default=list)
    total_debt = models.BigIntegerField(default=0)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by_chat = models.BigIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Haftalik qarz ro'yxati"
        verbose_name_plural = "Haftalik qarz ro'yxatlari"
