"""Finance models: LedgerEntry."""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class ImmutableQuerySet(models.QuerySet):
    """QuerySet that prevents update and delete operations."""

    def update(self, **kwargs):
        raise ValidationError("Moliyaviy yozuvlarni o'zgartirib bo'lmaydi.")

    def delete(self):
        raise ValidationError("Moliyaviy yozuvlarni o'chirib bo'lmaydi.")


class ImmutableManager(models.Manager):
    """Manager that prevents update and delete operations on querysets."""

    def get_queryset(self):
        return ImmutableQuerySet(self.model, using=self._db)


class LedgerEntry(models.Model):
    """Moliyaviy operatsiyalar kitobi (o'zgartirib bo'lmaydigan yozuvlar)."""

    class Kind(models.TextChoices):
        CHARGE = "charge", "To'lov hisobi (minus)"
        PAYMENT = "payment", "To'lov kiritildi (plus)"
        EXCUSED_CREDIT = "excused_credit", "Sababli kelmagan uchun qaytarish (plus)"
        ADJUSTMENT = "adjustment", "Qo'lda tuzatish"
        REFUND = "refund", "Pul qaytarildi (minus)"

    class PaymentMethod(models.TextChoices):
        CASH = "cash", "Naqd"
        CARD = "card", "Plastik karta"
        CLICK = "click", "Click"
        PAYME = "payme", "Payme"
        UZUM = "uzum", "Uzum"
        TRANSFER = "transfer", "Pul o'tkazmasi"

    student = models.ForeignKey(
        "students.Student",
        on_delete=models.PROTECT,
        related_name="ledger_entries",
        verbose_name="O'quvchi",
    )
    group = models.ForeignKey(
        "groups.Group",
        on_delete=models.PROTECT,
        related_name="ledger_entries",
        verbose_name="Guruh",
    )
    enrollment = models.ForeignKey(
        "groups.Enrollment",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="ledger_entries",
        verbose_name="A'zolik",
    )
    lesson = models.ForeignKey(
        "schedule.Lesson",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="ledger_entries",
        verbose_name="Dars",
    )
    kind = models.CharField(
        max_length=20,
        choices=Kind.choices,
        verbose_name="Turi",
    )
    amount = models.BigIntegerField(
        verbose_name="Summa (so'm)",
        help_text="Charge: manfiy; Payment/Excused_credit: musbat; Adjustment: istalgan",
    )
    method = models.CharField(
        max_length=20,
        choices=PaymentMethod.choices,
        blank=True,
        default="",
        verbose_name="To'lov usuli",
        help_text="Faqat payment turi uchun",
    )
    entry_date = models.DateField(verbose_name="Yozuv sanasi")
    comment = models.TextField(blank=True, default="", verbose_name="Izoh")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_ledger_entries",
        verbose_name="Kim yaratdi",
    )
    reverses = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reversed_by",
        verbose_name="Qaytarilgan yozuv",
        help_text="Excused_credit uchun: qaysi charge ni qaytaradi",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Yaratilgan vaqti")
    idempotency_key = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        verbose_name="Takroriy yuborishdan himoya kaliti",
        help_text="Formadan keladigan bir martalik kalit: bir xil kalit bilan ikkinchi to'lov yaratilmaydi",
    )

    objects = ImmutableManager()

    class Meta:
        verbose_name = "Moliyaviy yozuv"
        verbose_name_plural = "Moliyaviy yozuvlar"
        ordering = ["-entry_date", "-created_at"]
        constraints = [
            models.CheckConstraint(condition=models.Q(kind__in=["charge", "refund"], amount__lt=0) | models.Q(kind__in=["payment", "excused_credit"], amount__gt=0) | models.Q(kind="adjustment"), name="ledger_amount_sign_by_kind"),
            # Bir enrollment-lesson juftligi uchun faqat bitta charge
            models.UniqueConstraint(
                fields=["enrollment", "lesson"],
                condition=models.Q(kind="charge"),
                name="unique_charge_per_enrollment_lesson",
            ),
            # Bir charge uchun faqat bitta excused_credit
            models.UniqueConstraint(
                fields=["reverses"],
                condition=models.Q(kind="excused_credit", reverses__isnull=False),
                name="unique_excused_credit_per_charge",
            ),
            # Formani ikki marta yuborish ikkinchi to'lov yaratmasligi uchun
            models.UniqueConstraint(
                fields=["idempotency_key"],
                condition=models.Q(idempotency_key__isnull=False),
                name="unique_ledger_idempotency_key",
            ),
        ]
        indexes = [
            models.Index(fields=["student", "group"], name="idx_ledger_student_group"),
            models.Index(fields=["group", "entry_date"], name="idx_ledger_group_date"),
            models.Index(fields=["kind"], name="idx_ledger_kind"),
        ]

    def __str__(self):
        return (
            f"{self.student} — {self.group.name} "
            f"[{self.get_kind_display()}] {self.amount:,} so'm ({self.entry_date})"
        )

    def save(self, *args, **kwargs):
        """Prevent updates to existing records."""
        if self.pk is not None:
            raise ValidationError("Moliyaviy yozuvni o'zgartirib bo'lmaydi.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """Prevent deletion of records."""
        raise ValidationError("Moliyaviy yozuvni o'chirib bo'lmaydi.")


class PayrollMonth(models.Model):
    """Ish haqi oylik hisobi (yopilgandan keyin o'zgarmas)."""

    year = models.PositiveIntegerField(verbose_name="Yil")
    month = models.PositiveSmallIntegerField(verbose_name="Oy (1-12)")
    closed_at = models.DateTimeField(
        null=True, blank=True, verbose_name="Yopilgan vaqti"
    )
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="closed_payroll_months",
        verbose_name="Kim yopdi",
    )

    objects = ImmutableManager()

    class Meta:
        verbose_name = "Ish haqi oyi"
        verbose_name_plural = "Ish haqi oylari"
        constraints = [
            models.UniqueConstraint(
                fields=["year", "month"], name="unique_payroll_year_month"
            )
        ]
        ordering = ["-year", "-month"]

    def __str__(self):
        return f"{self.year}-{self.month:02d}"

    @property
    def is_closed(self) -> bool:
        return self.closed_at is not None

    def save(self, *args, **kwargs):
        """Prevent updates to months already closed in the database.

        Checks the stored value, not the in-memory one, so the
        open -> closed transition (closed_at set on an existing row) is allowed.
        """
        if self.pk is not None:
            stored_closed_at = (
                PayrollMonth._base_manager.filter(pk=self.pk)
                .values_list("closed_at", flat=True)
                .first()
            )
            if stored_closed_at is not None:
                raise ValidationError("Yopilgan oylik hisobni o'zgartirib bo'lmaydi.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """Prevent deletion."""
        raise ValidationError("Oylik hisobni o'chirib bo'lmaydi.")


class PayrollLine(models.Model):
    """O'qituvchining bir oylik ish haqi satri (faqat yopilgandan keyin yaratiladi)."""

    class Kind(models.TextChoices):
        PERCENT = "percent", "Foiz asosida"
        FIXED = "fixed", "Belgilangan oylik"

    month = models.ForeignKey(
        PayrollMonth,
        on_delete=models.PROTECT,
        related_name="lines",
        verbose_name="Oy",
    )
    teacher = models.ForeignKey(
        "teachers.Teacher",
        on_delete=models.PROTECT,
        related_name="payroll_lines",
        verbose_name="O'qituvchi",
    )
    group = models.ForeignKey(
        "groups.Group",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="payroll_lines",
        verbose_name="Guruh",
        help_text="Faqat foiz asosida to'lov uchun",
    )
    kind = models.CharField(
        max_length=20, choices=Kind.choices, verbose_name="Turi"
    )
    base_amount = models.BigIntegerField(
        verbose_name="Asos summasi (so'm)",
        help_text="Foiz uchun: to'lovlar yig'indisi; Fixed uchun: oylik maosh",
    )
    percent = models.PositiveSmallIntegerField(
        null=True, blank=True, verbose_name="Foiz (%)", help_text="Faqat foiz turi uchun"
    )
    amount = models.BigIntegerField(
        verbose_name="Hisoblangan summa (so'm)",
        help_text="Foiz uchun: base * percent // 100; Fixed uchun: base",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Yaratilgan vaqti")

    objects = ImmutableManager()

    class Meta:
        verbose_name = "Ish haqi satri"
        verbose_name_plural = "Ish haqi satrlari"
        ordering = ["teacher__full_name", "group__name"]

    def __str__(self):
        if self.group:
            return f"{self.teacher.full_name} — {self.group.name}: {self.amount:,} so'm"
        return f"{self.teacher.full_name} (fixed): {self.amount:,} so'm"

    def save(self, *args, **kwargs):
        """Prevent updates."""
        if self.pk is not None:
            raise ValidationError("Ish haqi satrini o'zgartirib bo'lmaydi.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """Prevent deletion."""
        raise ValidationError("Ish haqi satrini o'chirib bo'lmaydi.")


class PayrollAdjustment(models.Model):
    """Oylik ish haqi tuzatishi (faqat yopilmagan oy uchun)."""

    month = models.ForeignKey(
        PayrollMonth,
        on_delete=models.PROTECT,
        related_name="adjustments",
        verbose_name="Oy",
    )
    teacher = models.ForeignKey(
        "teachers.Teacher",
        on_delete=models.PROTECT,
        related_name="payroll_adjustments",
        verbose_name="O'qituvchi",
    )
    amount = models.BigIntegerField(
        verbose_name="Summa (so'm)",
        help_text="Musbat yoki manfiy (qo'shish yoki kamaytirish)",
    )
    comment = models.TextField(verbose_name="Izoh", help_text="Sabab majburiy")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_payroll_adjustments",
        verbose_name="Kim yaratdi",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Yaratilgan vaqti")

    objects = ImmutableManager()

    class Meta:
        verbose_name = "Ish haqi tuzatishi"
        verbose_name_plural = "Ish haqi tuzatishlari"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.teacher.full_name} ({self.month}): {self.amount:,} so'm"

    def save(self, *args, **kwargs):
        """Prevent updates."""
        if self.pk is not None:
            raise ValidationError("Ish haqi tuzatishini o'zgartirib bo'lmaydi.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """Prevent deletion."""
        raise ValidationError("Ish haqi tuzatishini o'chirib bo'lmaydi.")


class SalaryPayout(models.Model):
    """O'qituvchiga ish haqi to'lovi."""

    teacher = models.ForeignKey(
        "teachers.Teacher",
        on_delete=models.PROTECT,
        related_name="salary_payouts",
        verbose_name="O'qituvchi",
    )
    month = models.ForeignKey(
        PayrollMonth,
        on_delete=models.PROTECT,
        related_name="payouts",
        verbose_name="Qaysi oy uchun",
    )
    amount = models.BigIntegerField(
        verbose_name="To'langan summa (so'm)", help_text="Musbat son"
    )
    method = models.CharField(
        max_length=20,
        choices=LedgerEntry.PaymentMethod.choices,
        verbose_name="To'lov usuli",
    )
    paid_date = models.DateField(verbose_name="To'langan sana")
    comment = models.TextField(blank=True, default="", verbose_name="Izoh")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_salary_payouts",
        verbose_name="Kim to'ladi",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Yaratilgan vaqti")

    objects = ImmutableManager()

    class Meta:
        verbose_name = "Ish haqi to'lovi"
        verbose_name_plural = "Ish haqi to'lovlari"
        ordering = ["-paid_date", "-created_at"]
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="payout_amount_positive"),
        ]

    def __str__(self):
        return f"{self.teacher.full_name} ({self.month}): {self.amount:,} so'm ({self.paid_date})"

    def clean(self):
        """Validate that amount is positive."""
        if self.amount is not None and self.amount <= 0:
            raise ValidationError({"amount": "To'lov summasi musbat bo'lishi kerak."})

    def save(self, *args, **kwargs):
        """Prevent updates."""
        if self.pk is not None:
            raise ValidationError("Ish haqi to'lovini o'zgartirib bo'lmaydi.")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """Prevent deletion."""
        raise ValidationError("Ish haqi to'lovini o'chirib bo'lmaydi.")


class ExpenseCategory(models.Model):
    """Xarajat yoki kirim bo'limi (kategoriya)."""

    class Type(models.TextChoices):
        EXPENSE = "expense", "Chiqim (Xarajat)"
        INCOME = "income", "Kirim"

    name = models.CharField(max_length=100, verbose_name="Bo'lim nomi")
    category_type = models.CharField(
        max_length=20,
        choices=Type.choices,
        default=Type.EXPENSE,
        verbose_name="Turi",
    )
    is_system = models.BooleanField(
        default=False,
        verbose_name="Tizimli bo'lim",
        help_text="Tizim tomonidan avtomatik yaratilgan va o'chirilmaydigan",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Moliya bo'limi"
        verbose_name_plural = "Moliya bo'limlari"
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.get_category_type_display()})"


class ExpenseItem(models.Model):
    """Qo'shimcha chiqim yoki kirim yozuvi."""

    category = models.ForeignKey(
        ExpenseCategory,
        on_delete=models.PROTECT,
        related_name="items",
        verbose_name="Bo'lim",
    )
    amount = models.BigIntegerField(verbose_name="Summa (so'm)")
    entry_date = models.DateField(verbose_name="Sana")
    comment = models.TextField(blank=True, default="", verbose_name="Izoh")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_expense_items",
        verbose_name="Kim kiritdi",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Xarajat / Kirim yozuvi"
        verbose_name_plural = "Xarajat / Kirim yozuvlari"
        ordering = ["-entry_date", "-created_at"]
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="expense_amount_positive"),
        ]

    def __str__(self):
        return f"{self.category.name}: {self.amount:,} so'm ({self.entry_date})"


class ReceiptSettings(models.Model):
    """To'lov cheki sozlamalari (yagona konfiguratsiya yozuvi)."""

    class LogoPosition(models.TextChoices):
        TOP = "top", "Yuqorida"
        BOTTOM = "bottom", "Pastda"

    # Markaz nomi: menyu, kirish sahifasi, chek va Telegram xabarlarida ko'rsatiladi
    center_name = models.CharField(
        max_length=120,
        default="O'quv markazi",
        verbose_name="Markaz nomi",
    )

    # Chek yuqori qismi
    address = models.CharField(
        max_length=255,
        default="",
        blank=True,
        verbose_name="Manzil",
    )
    phone = models.CharField(
        max_length=50,
        default="",
        blank=True,
        verbose_name="Telefon raqami",
    )

    # Chek pastki qismi
    footer_text = models.CharField(
        max_length=255,
        default="",
        blank=True,
        verbose_name="Pastki qism matni",
    )

    # Logotip joylashuvi
    logo_position = models.CharField(
        max_length=10,
        choices=LogoPosition.choices,
        default=LogoPosition.TOP,
        verbose_name="Logotip joylashuvi",
    )

    # Ko'rinadigan maydonlar (toggles)
    show_logo = models.BooleanField(default=True, verbose_name="Logotip")
    # Markazning o'z logotipi (data: URI, PNG/JPEG/WebP, <=200 KB). Bo'sh: markaz nomining bosh harfi
    logo_data = models.TextField(blank=True, default="", verbose_name="Logotip rasmi")
    show_header = models.BooleanField(default=True, verbose_name="Chek yuqori qismi")
    show_address = models.BooleanField(default=True, verbose_name="Manzil")
    show_phone = models.BooleanField(default=True, verbose_name="Telefon raqami")
    show_issued_time = models.BooleanField(
        default=True, verbose_name="Chek chiqarilgan vaqt"
    )
    show_payment_id = models.BooleanField(default=True, verbose_name="To'lov ID")
    show_student_info = models.BooleanField(
        default=True, verbose_name="O'quvchi ma'lumotlari"
    )
    show_group_info = models.BooleanField(
        default=True, verbose_name="Guruh ma'lumotlari"
    )
    show_payment_method = models.BooleanField(
        default=True, verbose_name="To'lov shakli"
    )
    show_payment_date = models.BooleanField(
        default=True, verbose_name="To'lov sanasi"
    )
    show_cashier = models.BooleanField(default=True, verbose_name="Kassir")
    show_amount = models.BooleanField(default=True, verbose_name="Summa")
    show_footer = models.BooleanField(default=True, verbose_name="Pastki qism")
    show_footer_text = models.BooleanField(
        default=True, verbose_name="Pastki qism matni"
    )
    show_qr_code = models.BooleanField(default=True, verbose_name="QR kod")
    show_teacher = models.BooleanField(default=True, verbose_name="O'qituvchi")
    show_course_price = models.BooleanField(default=True, verbose_name="Kurs narxi")

    updated_at = models.DateTimeField(auto_now=True, verbose_name="Yangilangan vaqti")

    class Meta:
        verbose_name = "Chek sozlamalari"
        verbose_name_plural = "Chek sozlamalari"

    def __str__(self):
        return f"Chek sozlamalari ({self.address})"

    @classmethod
    def get_settings(cls):
        obj, _ = cls.objects.get_or_create(id=1)
        return obj
