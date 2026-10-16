"""Pytest fixtures and database setup."""

import pytest

from apps.accounts.models import User


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
