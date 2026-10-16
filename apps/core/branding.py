"""Markaz brendi: nom bitta joyda saqlanadi (Chek sozlamalari -> "Markaz nomi")."""

from django.core.cache import cache

DEFAULT_CENTER_NAME = "O'quv markazi"
PRODUCT_NAME = "Yurtora CRM"
_CACHE_KEY = "branding:center_name"
_CACHE_SECONDS = 60  # boshqa jarayonlar (gunicorn worker) yangi nomni ko'pi bilan 1 daqiqada ko'radi


def center_name() -> str:
    """Joriy o'quv markazining nomi. Har sahifada so'rov bo'lmasligi uchun keshlanadi."""
    name = cache.get(_CACHE_KEY)
    if name is None:
        from apps.finance.models import ReceiptSettings

        stored = ReceiptSettings.objects.filter(pk=1).values_list("center_name", flat=True).first()
        name = (stored or "").strip() or DEFAULT_CENTER_NAME
        cache.set(_CACHE_KEY, name, _CACHE_SECONDS)
    return name


def clear_center_name_cache(**kwargs):
    cache.delete(_CACHE_KEY)


def center_initial(name: str) -> str:
    for ch in name:
        if ch.isalnum():
            return ch.upper()
    return "Y"


def connect():
    from django.db.models.signals import post_delete, post_save

    from apps.finance.models import ReceiptSettings

    post_save.connect(clear_center_name_cache, sender=ReceiptSettings, dispatch_uid="branding_center_name_save")
    post_delete.connect(clear_center_name_cache, sender=ReceiptSettings, dispatch_uid="branding_center_name_delete")
