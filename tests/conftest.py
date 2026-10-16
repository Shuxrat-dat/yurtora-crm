"""Pytest fixtures and database setup."""

import os
import socket
import warnings

import pytest

from apps.accounts.models import User


def pytest_configure(config):
    """
    Testlar bazasini sozlash va tekshirish.

    - Standart bo'yicha testlar faqat PostgreSQL bazasida ishlaydi.
      Agar PostgreSQL mavjud bo'lmasa, testlar xatolik xabari bilan to'xtatiladi.
    - Zaxira SQLite faqat TEST_DB=sqlite muhit o'zgaruvchisi ko'rsatilganda ruxsat etiladi
      va pytest boshida ogohlantirish (warning) chiqaradi.
    """
    from django.conf import settings

    test_db_mode = os.environ.get("TEST_DB", "").lower()

    if test_db_mode == "sqlite":
        warnings.warn(
            "DIQQAT: Testlar zaxira varianti bo'lgan SQLite in-memory bazasida ishga tushirilmoqda! "
            "Asosiy testlar PostgreSQL bazasida o'tishi shart (docker compose exec web pytest).",
            UserWarning,
            stacklevel=2,
        )
    else:
        db_conf = settings.DATABASES.get("default", {})
        host = db_conf.get("HOST")
        port = int(db_conf.get("PORT") or 5432)

        if host and host not in ("localhost", "127.0.0.1"):
            try:
                socket.getaddrinfo(host, port)
            except OSError as exc:
                raise RuntimeError(
                    f"\n{'=' * 70}\n"
                    f"XATOLIK: PostgreSQL ma'lumotlar bazasiga ulanib bo'lmadi ({host}:{port})!\n"
                    f"Standart bo'yicha barcha testlar PostgreSQL bazasida ishlashi shart.\n\n"
                    f"Yechim:\n"
                    f"1. Bazani ishga tushiring: `docker compose up -d db`\n"
                    f"   va testlarni bajaring: `docker compose exec web pytest`\n"
                    f"2. Zaxira SQLite rejimida sinab ko'rish uchun: `TEST_DB=sqlite pytest`\n"
                    f"{'=' * 70}"
                ) from exc


@pytest.fixture
def password():
    return "SecretPass123!"


@pytest.fixture
def director_user(db, password):
    return User.objects.create_user(
        username="director_user",
        email="director@example.test",
        password=password,
        role=User.Role.DIRECTOR,
    )


@pytest.fixture
def admin_user(db, password):
    return User.objects.create_user(
        username="admin_user",
        email="admin@example.test",
        password=password,
        role=User.Role.ADMINISTRATOR,
    )


@pytest.fixture
def teacher_user(db, password):
    return User.objects.create_user(
        username="teacher_user",
        email="teacher@example.test",
        password=password,
        role=User.Role.TEACHER,
    )


@pytest.fixture
def inactive_user(db, password):
    return User.objects.create_user(
        username="inactive_user",
        email="inactive@example.test",
        password=password,
        role=User.Role.TEACHER,
        is_active=False,
    )


@pytest.fixture(autouse=True)
def _clear_cache():
    """Kesh (masalan, markaz nomi) testlar orasida saqlanib qolmasligi uchun."""
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()
