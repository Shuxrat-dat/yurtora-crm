"""Tests for authentication and role-based access control."""

import pytest
from django.contrib.auth import SESSION_KEY
from django.urls import reverse

from apps.accounts.models import User


@pytest.mark.django_db
class TestUserModel:
    """Тестирование кастомной модели User."""

    def test_password_is_hashed(self, password):
        """Пароль пользователя обязан сохраняться в хешированном виде, а не в открытом."""
        user = User.objects.create_user(
            username="hash_test_user",
            password=password,
        )
        assert user.password != password
        assert user.check_password(password) is True
        assert not user.check_password("wrong_password")

    def test_createsuperuser_assigns_director_role(self, password):
        """Суперпользователь, созданный через createsuperuser, автоматически получает роль director."""
        superuser = User.objects.create_superuser(
            username="super_director",
            email="super@example.test",
            password=password,
        )
        assert superuser.is_superuser is True
        assert superuser.is_staff is True
        assert superuser.role == User.Role.DIRECTOR

    def test_default_role_is_teacher(self, password):
        """Осознанный дефолт роли для новых пользователей — TEACHER (наименьшие привилегии)."""
        user = User.objects.create_user(
            username="default_role_user",
            password=password,
        )
        assert user.role == User.Role.TEACHER


@pytest.mark.django_db
class TestAuthentication:
    """Тестирование входа и выхода из системы."""

    def test_successful_login(self, client, teacher_user, password):
        """Успешный вход по логину и паролю перенаправляет на главную страницу."""
        login_url = reverse("accounts:login")
        response = client.post(
            login_url,
            {"username": teacher_user.username, "password": password},
        )
        assert response.status_code == 302
        assert response.url == reverse("accounts:home")
        assert client.session.get(SESSION_KEY) == str(teacher_user.pk)

    def test_inactive_user_cannot_login(self, client, inactive_user, password):
        """Неактивный пользователь (is_active=False) не может войти в систему."""
        login_url = reverse("accounts:login")
        response = client.post(
            login_url,
            {"username": inactive_user.username, "password": password},
        )
        # Форма возвращается с ошибкой (статус 200), сессия не создаётся
        assert response.status_code == 200
        assert SESSION_KEY not in client.session

    def test_logout_via_post(self, client, teacher_user):
        """Выход из системы через POST-запрос корректно завершает сессию пользователя."""
        client.force_login(teacher_user)
        assert client.session.get(SESSION_KEY) == str(teacher_user.pk)

        logout_url = reverse("accounts:logout")
        response = client.post(logout_url)
        assert response.status_code == 302
        assert response.url == reverse("accounts:login")
        assert SESSION_KEY not in client.session


@pytest.mark.django_db
class TestRoleAccessControl:
    """Тестирование разграничения прав доступа по ролям."""

    def test_anonymous_user_redirected_to_login(self, client):
        """Анонимный пользователь при попытке доступа к закрытым страницам перенаправляется на логин."""
        urls = [
            reverse("accounts:home"),
            reverse("accounts:admin_test"),
            reverse("accounts:teacher_test"),
        ]
        login_url = reverse("accounts:login")

        for url in urls:
            response = client.get(url)
            assert response.status_code == 302
            assert response.url.startswith(login_url)
            assert f"next={url}" in response.url

    def test_teacher_cannot_access_admin_page(self, client, teacher_user):
        """Учитель получает 403 Forbidden при попытке зайти в раздел администратора."""
        client.force_login(teacher_user)
        response = client.get(reverse("accounts:admin_test"))
        assert response.status_code == 403

    def test_teacher_can_access_teacher_page(self, client, teacher_user):
        """Учитель успешно заходит в свой раздел."""
        client.force_login(teacher_user)
        response = client.get(reverse("accounts:teacher_test"))
        assert response.status_code == 200

    def test_admin_cannot_access_teacher_page(self, client, admin_user):
        """Администратор получает 403 Forbidden при попытке зайти в раздел учителя."""
        client.force_login(admin_user)
        response = client.get(reverse("accounts:teacher_test"))
        assert response.status_code == 403

    def test_admin_can_access_admin_page(self, client, admin_user):
        """Администратор успешно заходит в раздел администратора."""
        client.force_login(admin_user)
        response = client.get(reverse("accounts:admin_test"))
        assert response.status_code == 200

    def test_director_has_access_to_all_pages(self, client, director_user):
        """Директор имеет доступ ко всем защищённым страницам CRM (главная, админская, учительская)."""
        client.force_login(director_user)

        home_resp = client.get(reverse("accounts:home"))
        assert home_resp.status_code == 200

        admin_resp = client.get(reverse("accounts:admin_test"))
        assert admin_resp.status_code == 200

        teacher_resp = client.get(reverse("accounts:teacher_test"))
        assert teacher_resp.status_code == 200
