"""Admin registration for attendance models."""

from django.contrib import admin

from apps.attendance.models import Attendance, AttendanceLog


class AttendanceLogInline(admin.TabularInline):
    model = AttendanceLog
    extra = 0
    readonly_fields = ["old_status", "new_status", "changed_by", "changed_at"]
    can_delete = False


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ["lesson", "student", "status", "marked_by", "updated_at"]
    list_filter = ["status", "lesson__date"]
    search_fields = ["student__first_name", "student__last_name", "lesson__group__name"]
    list_select_related = ["lesson", "student", "marked_by"]
    inlines = [AttendanceLogInline]


@admin.register(AttendanceLog)
class AttendanceLogAdmin(admin.ModelAdmin):
    list_display = ["attendance", "old_status", "new_status", "changed_by", "changed_at"]
    readonly_fields = ["changed_at"]
    list_select_related = ["attendance", "changed_by"]
