"""Root conftest: barcha testlar (tests/ va apps/) uchun umumiy sozlamalar."""

import pytest

# Barcha CRM testlari bitta test markazi sxemasida ishlaydi (django-tenants).
# "testserver" — Django test mijozining standart manzili: mavjud testlar o'zgartirishsiz shu markazga tushadi.
TEST_CENTER_SCHEMA = "testcenter"
TEST_CENTER_DOMAINS = ("testcenter.localhost", "testserver")
_test_center = None


def _ensure_test_center():
    """Test markazi va uning manzillari bor bo'lsin (transaction=True testlar public jadvallarni tozalaydi)."""
    from django.db import connection
    from django_tenants.utils import get_tenant_domain_model, get_tenant_model

    connection.set_schema_to_public()
    Center, Domain = get_tenant_model(), get_tenant_domain_model()
    center = Center.objects.filter(schema_name=TEST_CENTER_SCHEMA).first()
    if center is None:
        center = Center(schema_name=TEST_CENTER_SCHEMA, name="Test markaz")
        center.save()  # sxema mavjud bo'lsa qayta yaratilmaydi (check_if_exists)
    for i, domain in enumerate(TEST_CENTER_DOMAINS):
        Domain.objects.get_or_create(domain=domain, defaults={"tenant": center, "is_primary": i == 0})
    return center


@pytest.fixture(scope="session")
def django_db_setup(django_db_setup, django_db_blocker):
    global _test_center
    with django_db_blocker.unblock():
        _test_center = _ensure_test_center()


@pytest.fixture(autouse=True)
def _center_schema(django_db_setup, django_db_blocker):
    """Har bir test oldidan va keyin ulanish test markazi sxemasida.

    Markaz yozuvi har safar tekshiriladi: transaction=True test tugagach flush public jadvallarni ham
    tozalaydi, keyingi testlar markazni topa olishi uchun yozuvlar qayta tiklanadi (sxema qoladi).
    """
    from django.db import connection

    global _test_center
    with django_db_blocker.unblock():
        _test_center = _ensure_test_center()
    connection.set_tenant(_test_center)
    yield
    connection.set_tenant(_test_center)


@pytest.fixture
def public_client():
    """Yurtora ommaviy qismi (yurtora.uz) uchun mijoz: ROOT_DOMAIN testlarda "localhost"."""
    from django.test import Client

    return Client(HTTP_HOST="localhost")


@pytest.fixture(autouse=True)
def _neutral_production_security(settings):
    """
    Production xavfsizlik sozlamalari (HTTPS-redirect, secure cookie, manifest statika) DEBUG=False
    bo'lganda yoqiladi. Testlar muhitga (.env, CI) bog'liq bo'lmasligi uchun ular o'chiriladi.
    """
    settings.ROOT_DOMAIN = "localhost"
    settings.ALLOWED_HOSTS = ["localhost", ".localhost", "testserver"]
    settings.SECURE_SSL_REDIRECT = False
    settings.SESSION_COOKIE_SECURE = False
    settings.CSRF_COOKIE_SECURE = False
    settings.SECURE_HSTS_SECONDS = 0
    settings.STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
