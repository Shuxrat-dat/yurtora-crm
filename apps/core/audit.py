"""Harakatlar jurnali: joriy foydalanuvchini kuzatish va model signallari."""

from contextvars import ContextVar

from django.apps import apps
from django.contrib.auth.signals import (
    user_logged_in,
    user_logged_out,
    user_login_failed,
)
from django.db.models.signals import post_delete, post_save, pre_save

_current = ContextVar("audit_current", default=None)

# Jurnalga yoziladigan modellar ("app_label.ModelName"). Moliyaviy yozuvlar
# (LedgerEntry va h.k.) o'zgarmas bo'lgani uchun o'zi izni saqlaydi.
TRACKED_MODELS = [
    "accounts.User",
    "students.Student",
    "students.Guardian",
    "students.StudentGuardian",
    "teachers.Teacher",
    "courses.Course",
    "core.Goal",
    "groups.Group",
    "groups.Enrollment",
    "schedule.Room",
    "schedule.ScheduleSlot",
    "schedule.Lesson",
    "finance.ExpenseCategory",
    "finance.ExpenseItem",
    "finance.PayrollMonth",
    "finance.PayrollAdjustment",
    "finance.SalaryPayout",
    "finance.ReceiptSettings",
    "notifications.AdminChat",
]

# Bu modellarda yaratilish yozilmaydi (faqat o'zgarish va o'chirish): darslar oyiga yuzlab marta ommaviy
# yaratiladi va jurnalni to'ldirib yuborardi, muhimi — bekor qilish, ko'chirish va "o'tildi" holatlari.
SKIP_CREATE = {"schedule.Lesson"}

# Jurnalga tushmaydigan maydonlar
EXCLUDED_FIELDS = {"password", "last_login"}


class AuditMiddleware:
    """So'rov davomida joriy foydalanuvchi va IP ni saqlaydi."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        token = _current.set(
            {
                "user": user if user is not None and user.is_authenticated else None,
                "ip": request.META.get("REMOTE_ADDR"),
            }
        )
        try:
            return self.get_response(request)
        finally:
            _current.reset(token)


def _ctx():
    return _current.get() or {}


def _field_values(instance):
    return {
        f.name: str(getattr(instance, f.attname))
        for f in instance._meta.concrete_fields
        if f.name not in EXCLUDED_FIELDS and getattr(instance, f.attname) is not None
    }


def _in_center_schema() -> bool:
    """Jurnal faqat markaz sxemasida bor: Yurtora ommaviy qismida (public) yozilmaydi."""
    from django.db import connection
    from django_tenants.utils import get_public_schema_name

    return connection.schema_name != get_public_schema_name()


def _write(action, instance=None, changes=None, user=None, username="", ip=None):
    from .models import AuditLog

    if not _in_center_schema():
        return
    ctx = _ctx()
    user = user or ctx.get("user")
    AuditLog.objects.create(
        user=user,
        username=(user.get_username() if user else username)[:150],
        action=action,
        model=instance._meta.label if instance is not None else "",
        object_id=str(instance.pk) if instance is not None else "",
        object_repr=str(instance)[:255] if instance is not None else "",
        changes=changes or {},
        ip_address=ip or ctx.get("ip"),
    )


def _on_pre_save(sender, instance, raw=False, **kwargs):
    if raw or not _ctx().get("user") or instance.pk is None:
        return
    old = sender._default_manager.filter(pk=instance.pk).first()
    instance._audit_old = _field_values(old) if old else None


def _on_post_save(sender, instance, created, raw=False, **kwargs):
    # Faqat foydalanuvchi so'rovi doirasidagi o'zgarishlar yoziladi
    # (management buyruqlari, seed va testlar shovqin qilmasin).
    if raw or not _ctx().get("user"):
        return
    if created:
        if instance._meta.label in SKIP_CREATE:
            return
        _write("create", instance, {k: [None, v] for k, v in _field_values(instance).items()})
        return
    old = getattr(instance, "_audit_old", None) or {}
    new = _field_values(instance)
    diff = {k: [old.get(k), v] for k, v in new.items() if old.get(k) != v}
    if diff:
        _write("update", instance, diff)


def _on_post_delete(sender, instance, **kwargs):
    if _ctx().get("user"):
        _write("delete", instance, {k: [v, None] for k, v in _field_values(instance).items()})


def _on_login(sender, request, user, **kwargs):
    _write("login", user=user, ip=request.META.get("REMOTE_ADDR") if request else None)


def _on_logout(sender, request, user, **kwargs):
    if user:
        _write("logout", user=user, ip=request.META.get("REMOTE_ADDR") if request else None)


# Autentifikatsiyadan o'tmagan so'rovlar jurnalni to'ldirib yubormasligi uchun: bir daqiqada ko'pi bilan shuncha yozuv
LOGIN_FAILED_WINDOW_SECONDS = 60
LOGIN_FAILED_MAX_PER_WINDOW = 30


def _login_failed_flooded():
    from datetime import timedelta

    from django.utils import timezone

    from .models import AuditLog

    since = timezone.now() - timedelta(seconds=LOGIN_FAILED_WINDOW_SECONDS)
    if not _in_center_schema():
        return False
    return AuditLog.objects.filter(action="login_failed", created_at__gte=since).count() >= LOGIN_FAILED_MAX_PER_WINDOW


def log_password_change(target, by, own=False):
    """Parol o'zgarganini yozadi (parolning o'zi yoki xeshi yozilmaydi)."""
    _write(
        "update",
        target,
        {"password": ["***", "o'zini o'zi o'zgartirdi" if own else "direktor yangi parol o'rnatdi"]},
        user=by,
    )


def _on_login_failed(sender, credentials, request=None, **kwargs):
    if _login_failed_flooded():
        return
    _write(
        "login_failed",
        username=str((credentials or {}).get("username", "")),
        ip=request.META.get("REMOTE_ADDR") if request else None,
    )


def connect():
    for label in TRACKED_MODELS:
        model = apps.get_model(label)
        uid = f"audit_{label}"
        pre_save.connect(_on_pre_save, sender=model, dispatch_uid=f"{uid}_pre")
        post_save.connect(_on_post_save, sender=model, dispatch_uid=f"{uid}_save")
        post_delete.connect(_on_post_delete, sender=model, dispatch_uid=f"{uid}_del")
    user_logged_in.connect(_on_login, dispatch_uid="audit_login")
    user_logged_out.connect(_on_logout, dispatch_uid="audit_logout")
    user_login_failed.connect(_on_login_failed, dispatch_uid="audit_login_failed")
