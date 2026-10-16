"""Umumiy shablon filtrlari: {% load mv %}."""

import uuid

from django import template

register = template.Library()

NBSP = " "
MINUS = "−"


@register.filter
def som(value):
    """Summani o'qishga qulay ko'rinishga keltiradi: -4894543 -> "−4 894 543".

    Raqamlar orasida bo'linmaydigan bo'shliq (qator uzilmasin), manfiy son uchun haqiqiy minus belgisi.
    Son bo'lmagan qiymat o'zgarishsiz qaytariladi.
    """
    if value is None or value == "":
        return value
    try:
        number = round(float(value))
    except (TypeError, ValueError):
        return value
    sign = MINUS if number < 0 else ""
    return f"{sign}{abs(number):,}".replace(",", NBSP)


@register.filter
def som_abs(value):
    """Summani belgisiz ko'rsatadi (masalan, qarz musbat holda): -750000 -> "750 000"."""
    if value is None or value == "":
        return value
    try:
        return som(abs(float(value)))
    except (TypeError, ValueError):
        return value


TONES = 10


@register.filter
def tone(value):
    """Guruh uchun barqaror rang raqami (0-9): bir guruh hamma joyda bir xil rangda."""
    try:
        return int(value) % TONES
    except (TypeError, ValueError):
        return 0


@register.simple_tag
def idem_key():
    """Formaga bir martalik kalit: ikki marta yuborilgan to'lov ikkinchi marta saqlanmaydi."""
    return uuid.uuid4().hex
