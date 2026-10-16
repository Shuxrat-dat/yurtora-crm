"""Yurtora mijozlari: har bir o'quv markazi o'z PostgreSQL sxemasida (ma'lumotlar bir-biridan ajratilgan)."""

from django.db import models
from django_tenants.models import DomainMixin, TenantMixin


class Center(TenantMixin):
    """O'quv markazi. schema_name = manzildagi nom (masalan, ilm -> ilm.yurtora.uz)."""

    name = models.CharField(max_length=120, verbose_name="Markaz nomi")
    contact_name = models.CharField(max_length=120, blank=True, verbose_name="Mas'ul shaxs")
    contact_phone = models.CharField(max_length=30, blank=True, verbose_name="Telefon")
    is_active = models.BooleanField(default=True, verbose_name="Faol")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Ro'yxatdan o'tgan")

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
