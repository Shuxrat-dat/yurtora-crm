"""Ro'yxat filtrlari uchun umumiy yordamchilar: xavfsiz parametr o'qish va qidiruv.

Barcha funksiyalar noto'g'ri qiymatda xato ko'tarmaydi (500 o'rniga standart qiymat).
"""

import re
from datetime import date

from django.db.models import Q

_PHONE_NOISE = re.compile(r"[\s\-()]+")


def parse_int(value, minimum=None, maximum=None):
    """Matnni butun songa aylantiradi. Noto'g'ri yoki chegaradan tashqari qiymat: None."""
    try:
        number = int(re.sub(r"\s+", "", str(value)))  # "1 500 000" ham qabul qilinadi
    except (TypeError, ValueError):
        return None
    if minimum is not None and number < minimum:
        return None
    if maximum is not None and number > maximum:
        return None
    return number


def parse_date(value):
    """YYYY-MM-DD matnini sanaga aylantiradi. Noto'g'ri qiymat: None."""
    try:
        return date.fromisoformat(str(value).strip())
    except (TypeError, ValueError):
        return None


def resolve_date_range(raw_from, raw_to, default_from, default_to):
    """Sana oralig'ini aniqlaydi (ikkala chegara ham kiritilgan kunni o'z ichiga oladi).

    - Hech biri kiritilmasa yoki noto'g'ri bo'lsa: standart oraliq.
    - Bitta chegara bo'sh bo'lsa, u standart oraliq bilan to'ldiriladi.
    - Teskari oraliq (boshlanish > tugash) almashtiriladi.
    """
    start = parse_date(raw_from)
    end = parse_date(raw_to)
    if start is None and end is None:
        return default_from, default_to
    if start is None:
        start = min(default_from, end)
    if end is None:
        end = max(default_to, start)
    if start > end:
        start, end = end, start
    return start, end


def resolve_year_month(raw_year, raw_month, today):
    """Yil (2000-2100) va oyni (1-12) xavfsiz o'qiydi; noto'g'ri bo'lsa joriy qiymat."""
    year = parse_int(raw_year, 2000, 2100) or today.year
    month = parse_int(raw_month, 1, 12) or today.month
    return year, month


def search_tokens(query):
    """Qidiruv so'rovini bo'sh joylar bo'yicha so'zlarga ajratadi."""
    return [token for token in (query or "").split() if token]


def person_search_q(query, prefix="", include_phone=True):
    """Ism, familiya, otasining ismi (va telefon) bo'yicha qidiruv sharti.

    Har bir so'z kamida bitta maydonga mos kelishi kerak, so'zlar tartibi muhim emas
    ("Ali Aliyev" va "aliyev ali" bir xil). Registr e'tiborga olinmaydi. Telefon
    bo'shliq, defis va qavslarsiz solishtiriladi. ``prefix`` bog'langan model uchun,
    masalan ``"student__"``.
    """
    condition = Q()
    for token in search_tokens(query):
        token_q = (
            Q(**{f"{prefix}first_name__icontains": token})
            | Q(**{f"{prefix}last_name__icontains": token})
            | Q(**{f"{prefix}middle_name__icontains": token})
        )
        if include_phone:
            digits = _PHONE_NOISE.sub("", token)
            if digits:
                token_q |= Q(**{f"{prefix}phone__icontains": digits})
        condition &= token_q
    return condition
