"""Admin configuration for custom User model."""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .forms import CustomUserChangeForm, CustomUserCreationForm
from .models import User

# Django admin panelining sarlavhalari (o'zbek tilida, lotin alifbosi)
admin.site.site_header = "Yurtora CRM Boshqaruvi"
admin.site.site_title = "Yurtora CRM Admin"
admin.site.index_title = "Tizimni boshqarish"

@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Foydalanuvchilar va ularning rollarini boshqarish admin interfeysi."""

    form = CustomUserChangeForm
    add_form = CustomUserCreationForm
    model = User

    list_display = (
        "username",
        "email",
        "first_name",
        "last_name",
        "role",
        "is_staff",
        "is_active",
    )
    list_filter = ("role", "is_staff", "is_superuser", "is_active")
    search_fields = ("username", "first_name", "last_name", "email")
    ordering = ("username",)

    # Foydalanuvchini tahrirlashda role maydonini ko'rsatish
    fieldsets = BaseUserAdmin.fieldsets + (("CRM dagi roli", {"fields": ("role",)}),)

    # Yangi foydalanuvchi yaratishda role maydonini ko'rsatish
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ("CRM dagi roli", {"fields": ("role",)}),
    )
