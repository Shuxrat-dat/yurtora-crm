"""Yurtora mijozlari: har bir o'quv markazi o'z PostgreSQL sxemasida (ma'lumotlar bir-biridan ajratilgan)."""

from django.conf import settings
from django.db import models
from django_tenants.models import DomainMixin, TenantMixin


class Plan(models.Model):
    """Tarif: oylik narx va limitlar. Bo'sh limit — cheklovsiz."""

    code = models.SlugField(max_length=30, unique=True, verbose_name="Kod")
    name = models.CharField(max_length=60, verbose_name="Nomi")
    price_month = models.PositiveBigIntegerField(verbose_name="Oylik narx (so'm)")
    max_students = models.PositiveIntegerField(null=True, blank=True, verbose_name="O'quvchilar limiti")
    max_users = models.PositiveIntegerField(null=True, blank=True, verbose_name="Foydalanuvchilar limiti")
    is_default = models.BooleanField(default=False, verbose_name="Yangi markazlar uchun (sinov davri)")
    is_active = models.BooleanField(default=True, verbose_name="Sotuvda")
    sort = models.PositiveSmallIntegerField(default=0, verbose_name="Tartib")

    class Meta:
        verbose_name = "Tarif"
        verbose_name_plural = "Tariflar"
        ordering = ["sort", "price_month"]

    def __str__(self):
        return self.name


class Center(TenantMixin):
    """O'quv markazi. schema_name = manzildagi nom (masalan, ilm -> ilm.yurtora.uz)."""

    name = models.CharField(max_length=120, verbose_name="Markaz nomi")
    contact_name = models.CharField(max_length=120, blank=True, verbose_name="Mas'ul shaxs")
    contact_phone = models.CharField(max_length=30, blank=True, verbose_name="Telefon")
    is_active = models.BooleanField(default=True, verbose_name="Faol")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Ro'yxatdan o'tgan")

    # Obuna: tarif va muddatlar. Holat (sinov, faol, ko'rish rejimi, bloklangan) billing.subscription() da
    plan = models.ForeignKey(Plan, null=True, blank=True, on_delete=models.PROTECT, related_name="centers", verbose_name="Tarif")
    trial_ends_at = models.DateField(null=True, blank=True, verbose_name="Sinov davri tugaydi")
    paid_until = models.DateField(null=True, blank=True, verbose_name="To'langan sana (shu kungacha)")

    auto_create_schema = True
    # Markaz yozuvi o'chirilsa ham sxema (o'quvchilar, to'lovlar) tasodifan yo'qolmasin
    auto_drop_schema = False

    class Meta:
        verbose_name = "Markaz"
        verbose_name_plural = "Markazlar"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.schema_name})"

    @property
    def primary_domain(self) -> str:
        domain = self.domains.filter(is_primary=True).first()
        return domain.domain if domain else ""


class Domain(DomainMixin):
    """Markaz manzili (masalan, ilm.yurtora.uz)."""

    class Meta:
        verbose_name = "Domen"
        verbose_name_plural = "Domenlar"


class SubscriptionPayment(models.Model):
    """Obuna to'lovi (o'zgarmas yozuv): markaz muddatini N oyga uzaytiradi."""

    class Method(models.TextChoices):
        MANUAL = "manual", "Qo'lda (naqd / bank o'tkazmasi)"
        PAYME = "payme", "Payme"
        CLICK = "click", "Click"

    center = models.ForeignKey(Center, on_delete=models.PROTECT, related_name="payments", verbose_name="Markaz")
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="payments", verbose_name="Tarif")
    months = models.PositiveSmallIntegerField(verbose_name="Oylar soni")
    amount = models.PositiveBigIntegerField(verbose_name="Summa (so'm)")
    method = models.CharField(max_length=20, choices=Method.choices, default=Method.MANUAL, verbose_name="Usul")
    period_start = models.DateField(verbose_name="Davr boshi")
    period_end = models.DateField(verbose_name="Davr oxiri (shu kungacha)")
    comment = models.CharField(max_length=255, blank=True, verbose_name="Izoh")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", verbose_name="Kiritgan"
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Kiritilgan vaqt")

    class Meta:
        verbose_name = "Obuna to'lovi"
        verbose_name_plural = "Obuna to'lovlari"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(condition=models.Q(months__gte=1), name="subscription_payment_months_positive"),
            models.CheckConstraint(condition=models.Q(period_end__gt=models.F("period_start")), name="subscription_payment_period_valid"),
        ]

    def __str__(self):
        return f"{self.center.name}: {self.months} oy, {self.period_end} gacha"


class Suggestion(models.Model):
    """Markazdan Yurtora egasiga yuborilgan taklif. Doim "public" sxemada saqlanadi (egasi admin panelda ko'radi)."""

    class Status(models.TextChoices):
        NEW = "new", "Yangi"
        READ = "read", "Ko'rildi"
        DONE = "done", "Hisobga olindi"

    center = models.ForeignKey(Center, on_delete=models.CASCADE, related_name="suggestions", verbose_name="Markaz")
    author = models.CharField(max_length=150, verbose_name="Kim yubordi")
    role = models.CharField(max_length=30, blank=True, verbose_name="Roli")
    text = models.TextField(max_length=2000, verbose_name="Taklif")
    contact = models.CharField(max_length=60, blank=True, verbose_name="Aloqa (ixtiyoriy)")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.NEW, db_index=True, verbose_name="Holati")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Yuborilgan vaqti")

    class Meta:
        verbose_name = "Taklif"
        verbose_name_plural = "Takliflar"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.center.name}: {self.text[:40]}"
