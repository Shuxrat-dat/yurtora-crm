"""Markazni ro'yxatdan o'tkazish va manzil (subdomen) qoidalari."""

import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django_tenants.utils import get_public_schema_name, schema_context

SUBDOMAIN_RE = re.compile(r"^[a-z][a-z0-9]{2,29}$")
# Yurtora o'zi uchun saqlab qo'yilgan manzillar
RESERVED_SUBDOMAINS = frozenset({
    "www", "admin", "api", "app", "mail", "smtp", "imap", "ftp", "static", "media", "public",
    "yurtora", "support", "help", "billing", "pay", "payment", "status", "test", "demo", "dev",
    "staging", "login", "signup", "account", "accounts", "cdn", "docs", "blog",
})


def normalize_subdomain(value: str) -> str:
    return (value or "").strip().lower()


def validate_subdomain(value: str) -> None:
    from .models import Center

    if not SUBDOMAIN_RE.match(value):
        raise ValidationError(
            "Manzil 3-30 belgidan iborat bo'lsin: faqat lotin harflari va raqamlar, harf bilan boshlanadi."
        )
    if value in RESERVED_SUBDOMAINS or value == get_public_schema_name():
        raise ValidationError("Bu manzil band. Boshqasini tanlang.")
    with schema_context(get_public_schema_name()):
        if Center.objects.filter(schema_name=value).exists():
            raise ValidationError("Bu manzil band. Boshqasini tanlang.")


def center_domain(subdomain: str) -> str:
    return f"{subdomain}.{settings.ROOT_DOMAIN}"


def public_hosts() -> set[str]:
    root = settings.ROOT_DOMAIN
    return {root, f"www.{root}"}


def is_public_schema() -> bool:
    return connection.schema_name == get_public_schema_name()


def active_center_schemas() -> list[str]:
    """To'liq ishlayotgan markazlar sxemalari (bot va fon vazifalari): obuna tugaganlarga xabar yuborilmaydi."""
    from . import billing
    from .models import Center

    with schema_context(get_public_schema_name()):
        centers = Center.objects.filter(is_active=True).exclude(schema_name=get_public_schema_name())
        return [c.schema_name for c in centers if billing.subscription(c).full_access]


def center_schema_is_active(schema: str) -> bool:
    from .models import Center

    if not SUBDOMAIN_RE.match(schema or ""):
        return False
    with schema_context(get_public_schema_name()):
        return Center.objects.filter(schema_name=schema, is_active=True).exists()


def create_center(*, name, subdomain, director_username, director_password, contact_name="", contact_phone=""):
    """
    Yangi markaz: sxema (barcha jadvallar bilan), manzil, direktor akkaunti va markaz nomi.
    Hammasi bitta tranzaksiyada: xato bo'lsa, yarim yaratilgan markaz qolmaydi.
    """
    from apps.accounts.models import User
    from apps.finance.models import ReceiptSettings

    from .models import Center, Domain

    subdomain = normalize_subdomain(subdomain)
    validate_subdomain(subdomain)
    with schema_context(get_public_schema_name()), transaction.atomic():
        from datetime import timedelta

        from django.utils import timezone

        from . import billing
        from .models import Plan

        center = Center(
            schema_name=subdomain,
            name=name.strip(),
            contact_name=contact_name.strip(),
            contact_phone=contact_phone.strip(),
            plan=Plan.objects.filter(is_default=True, is_active=True).first(),
            trial_ends_at=timezone.localdate() + timedelta(days=billing.TRIAL_DAYS - 1),
        )
        center.save()  # sxema yaratiladi va markaz migratsiyalari bajariladi
        Domain.objects.create(domain=center_domain(subdomain), tenant=center, is_primary=True)
        with schema_context(center.schema_name):
            User.objects.create_user(
                username=director_username,
                password=director_password,
                role=User.Role.DIRECTOR,
                first_name=contact_name.strip()[:150],
            )
            receipt = ReceiptSettings.get_settings()
            receipt.center_name = center.name
            receipt.phone = contact_phone.strip()
            receipt.save()
    return center
