"""Finance admin."""

from django.contrib import admin

from apps.finance.models import LedgerEntry, ReceiptSettings


@admin.register(ReceiptSettings)
class ReceiptSettingsAdmin(admin.ModelAdmin):
    """ReceiptSettings admin."""

    list_display = ["address", "phone", "logo_position", "updated_at"]


@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    """LedgerEntry admin."""

    list_display = [
        "student",
        "group",
        "kind",
        "amount",
        "entry_date",
        "method",
        "created_at",
    ]
    list_filter = ["kind", "entry_date", "group"]
    search_fields = ["student__first_name", "student__last_name", "comment"]
    readonly_fields = [
        "student",
        "group",
        "enrollment",
        "lesson",
        "kind",
        "amount",
        "method",
        "entry_date",
        "comment",
        "created_by",
        "reverses",
        "created_at",
    ]

    def has_add_permission(self, request):
        """Prevent adding via admin (use services)."""
        return False

    def has_change_permission(self, request, obj=None):
        """Prevent changes via admin."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Prevent deletion via admin."""
        return False
