"""Root conftest: barcha testlar (tests/ va apps/) uchun umumiy sozlamalar."""

import pytest


@pytest.fixture(autouse=True)
def _neutral_production_security(settings):
    """
    Production xavfsizlik sozlamalari (HTTPS-redirect, secure cookie, manifest statika) DEBUG=False
    bo'lganda yoqiladi. Testlar muhitga (.env, CI) bog'liq bo'lmasligi uchun ular o'chiriladi.
    """
    settings.SECURE_SSL_REDIRECT = False
    settings.SESSION_COOKIE_SECURE = False
    settings.CSRF_COOKIE_SECURE = False
    settings.SECURE_HSTS_SECONDS = 0
    settings.STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
