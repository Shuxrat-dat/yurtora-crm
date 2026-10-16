"""Shablonlar uchun umumiy o'zgaruvchilar."""

from django.conf import settings

from apps.core.branding import PRODUCT_NAME, center_initial, center_name

_VERSIONED_ASSETS = ("css/app.css", "js/app.js")


def asset_version(request):
    """Statik fayllar o'zgarganda brauzer keshini yangilash uchun versiya (?v=...).

    Versiya app.css va app.js fayllarining oxirgi o'zgarish vaqtidan olinadi.
    """
    mtimes = []
    for relative_path in _VERSIONED_ASSETS:
        try:
            mtimes.append(int((settings.BASE_DIR / "static" / relative_path).stat().st_mtime))
        except OSError:
            continue
    return {"ASSET_VERSION": max(mtimes, default=0)}


def branding(request):
    """Menyu, kirish sahifasi va chek uchun markaz nomi va mahsulot nomi."""
    name = center_name()
    return {"CENTER_NAME": name, "CENTER_INITIAL": center_initial(name), "PRODUCT_NAME": PRODUCT_NAME}
