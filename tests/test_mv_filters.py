"""Shablon filtri som: summalarni o'qishga qulay ko'rinishda chiqarish."""

import pytest
from django.template import Context, Template

from apps.core.templatetags.mv import som, som_abs

NBSP = " "


@pytest.mark.parametrize(
    "value,expected",
    [
        (4894543, f"4{NBSP}894{NBSP}543"),
        (-750000, f"−750{NBSP}000"),
        (0, "0"),
        (999, "999"),
        ("1500000", f"1{NBSP}500{NBSP}000"),
        (1234.6, f"1{NBSP}235"),
        (None, None),
        ("", ""),
        ("abc", "abc"),
    ],
)
def test_som(value, expected):
    assert som(value) == expected


def test_som_abs_drops_sign():
    assert som_abs(-750000) == f"750{NBSP}000"


def test_filter_available_in_templates():
    html = Template("{% load mv %}{{ x|som }} so'm").render(Context({"x": -300000}))
    assert html == f"−300{NBSP}000 so'm"
