"""Umumiy forma maydonlari."""

import re

from django import forms

# Oddiy, bo'linmas (NBSP) va ingichka bo'shliqlar: "1 500 000" ham qabul qilinadi
_SPACES = re.compile(r"[\s  ]+")


class MoneyInput(forms.TextInput):
    """Summa maydoni: brauzerda xonalar bo'shliq bilan ajratiladi (static/js/app.js, data-money)."""

    def __init__(self, attrs=None):
        base = {"inputmode": "numeric", "autocomplete": "off", "data-money": ""}
        super().__init__({**base, **(attrs or {})})

    def format_value(self, value):
        if value in (None, ""):
            return None
        try:
            return f"{int(_SPACES.sub('', str(value))):,}".replace(",", " ")
        except ValueError:
            return str(value)


class MoneyField(forms.IntegerField):
    """Butun so'm: "500 000" va "500000" bir xil qabul qilinadi."""

    widget = MoneyInput

    def to_python(self, value):
        if isinstance(value, str):
            value = _SPACES.sub("", value)
        return super().to_python(value)

    def widget_attrs(self, widget):
        # min/max/step atributlari matn maydonida kerak emas (tekshiruv serverda)
        attrs = super().widget_attrs(widget)
        for key in ("min", "max", "step"):
            attrs.pop(key, None)
        return attrs
