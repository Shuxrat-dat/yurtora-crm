"""Django admin configuration for Group, Enrollment, and EnrollmentLog."""

from django.contrib import admin

from apps.groups.models import Enrollment, EnrollmentLog, Group


class EnrollmentInline(admin.TabularInline):
    model = Enrollment
    extra = 0
    fields = ("student", "status", "start_date", "paid_from", "frozen_from", "end_date")


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = ("name", "course", "teacher", "status", "max_students", "active_students_count", "start_date")
    list_filter = ("status", "course", "teacher")
    search_fields = ("name", "course__name", "teacher__full_name")
    inlines = [EnrollmentInline]


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = ("student", "group", "status", "start_date", "paid_from", "frozen_from", "end_date")
    list_filter = ("status", "group")
    search_fields = ("student__first_name", "student__last_name", "student__phone", "group__name")


@admin.register(EnrollmentLog)
class EnrollmentLogAdmin(admin.ModelAdmin):
    list_display = ("enrollment", "action", "old_status", "new_status", "changed_by", "changed_at")
    list_filter = ("action", "new_status")
    search_fields = ("enrollment__student__first_name", "enrollment__student__last_name", "enrollment__group__name")
    readonly_fields = ("enrollment", "action", "old_status", "new_status", "changed_by", "changed_at", "note")
