"""Oy nomlari (o'zbekcha) va oylar bilan ishlash yordamchilari."""

from datetime import date

MONTH_NAMES = [
    "Yanvar", "Fevral", "Mart", "Aprel", "May", "Iyun",
    "Iyul", "Avgust", "Sentabr", "Oktabr", "Noyabr", "Dekabr",
]


def month_start(value: date) -> date:
    return value.replace(day=1)


def add_months(value: date, n: int) -> date:
    """Oyning 1-sanasini n oyga siljitadi."""
    index = value.year * 12 + (value.month - 1) + n
    return date(index // 12, index % 12 + 1, 1)


def month_label(value: date, with_year=True) -> str:
    return f"{MONTH_NAMES[value.month - 1]} {value.year}" if with_year else MONTH_NAMES[value.month - 1]


def month_value(value: date) -> str:
    """HTML forma uchun: 2026-10."""
    return f"{value.year}-{value.month:02d}"


def parse_month(text):
    """'2026-10' -> date(2026, 10, 1); noto'g'ri qiymatda ValueError."""
    year, month = str(text).strip().split("-")
    return date(int(year), int(month), 1)
