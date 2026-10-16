from django.contrib import admin

from .models import Center, Domain


class DomainInline(admin.TabularInline):
    model = Domain
    extra = 0


@admin.register(Center)
class CenterAdmin(admin.ModelAdmin):
    list_display = ("name", "schema_name", "contact_name", "contact_phone", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name", "schema_name", "contact_name", "contact_phone")
    readonly_fields = ("schema_name", "created_at")
    inlines = [DomainInline]

    def has_delete_permission(self, request, obj=None):
        return False  # markazni o'chirish o'rniga bloklang (is_active)
