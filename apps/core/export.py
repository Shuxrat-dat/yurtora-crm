"""Excel/CSV eksport xavfsizligi: formula injection (CSV/Formula Injection, OWASP) dan himoya.

Excel va LibreOffice "=", "+", "-", "@" bilan boshlangan matnni formula deb bajaradi. O'quvchi ismi,
izoh yoki maktab nomi orqali kiritilgan "=HYPERLINK(...)" eksport qilingan faylni ochgan odamga zarar keltirishi
mumkin. Bunday qiymatlar oldiga `'` qo'yiladi (matn sifatida ko'rinadi).
"""

import re

_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")
# "+998 90 123-45-67", "-5000" kabi telefon/son ko'rinishidagi qiymatlar formula emas: o'zgartirilmaydi
_PHONE_OR_NUMBER = re.compile(r"^[+-]?[\d\s().,-]+$")


def safe_cell(value):
    """Matn qiymatni formula sifatida bajarilishidan himoyalaydi; boshqa turlarni (son, sana) o'zgartirmaydi."""
    if isinstance(value, str) and value.startswith(_TRIGGERS) and not _PHONE_OR_NUMBER.match(value):
        return "'" + value
    return value


def safe_row(values):
    return [safe_cell(value) for value in values]


class SafeCsvWriter:
    """Обёртка над csv.writer: каждая строка проходит через safe_row."""

    def __init__(self, writer):
        self._writer = writer

    def writerow(self, row):
        return self._writer.writerow(safe_row(row))

    def writerows(self, rows):
        for row in rows:
            self.writerow(row)
