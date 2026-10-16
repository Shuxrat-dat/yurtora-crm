"""Role-based access control mixins and decorators."""

from collections.abc import Sequence
from functools import wraps

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied

from .models import User


def user_has_role(user, allowed_roles: Sequence[str]) -> bool:
    """
    Foydalanuvchida ruxsat etilgan rollardan biri mavjudligini tekshiradi.

    Global qoida: DIRECTOR roliga ega foydalanuvchi CRM ning
    barcha himoyalangan sahifalariga cheklovlarsiz kirish huquqiga ega.
    """
    if not user.is_authenticated:
        return False
    if user.role == User.Role.DIRECTOR:
        return True
    return user.role in allowed_roles


class RoleRequiredMixin:
    """
    CBV Mixin rollar bo'yicha kirishni cheklash uchun.

    - Foydalanuvchi tizimga kirmagan bo'lsa -> kirish sahifasiga yo'naltiriladi.
    - Tizimga kirgan, lekin roli to'g'ri kelmasa (va direktor bo'lmasa) -> 403 (PermissionDenied).
    """

    allowed_roles: Sequence[str] = ()

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(
                request.get_full_path(),
                login_url=settings.LOGIN_URL,
            )

        if not user_has_role(request.user, self.allowed_roles):
            raise PermissionDenied("Ushbu bo'limga kirish uchun huquq yetarli emas.")

        return super().dispatch(request, *args, **kwargs)


def role_required(allowed_roles: Sequence[str]):
    """
    FBV (funksiya-ko'rinishlar) uchun rollar bo'yicha kirishni cheklovchi dekorator.
    """

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(
                    request.get_full_path(),
                    login_url=settings.LOGIN_URL,
                )

            if not user_has_role(request.user, allowed_roles):
                raise PermissionDenied(
                    "Ushbu bo'limga kirish uchun huqu etarli emas."
                )

            return view_func(request, *args, **kwargs)

        return _wrapped_view

    return decorator
