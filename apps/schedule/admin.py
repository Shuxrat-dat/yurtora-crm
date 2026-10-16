"""Admin registration for schedule models."""

from django.contrib import admin

from apps.schedule.models import Lesson, Room, ScheduleSlot


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ["name", "is_active", "created_at"]
    list_filter = ["is_active"]
    search_fields = ["name"]


@admin.register(ScheduleSlot)
class ScheduleSlotAdmin(admin.ModelAdmin):
    list_display = ["group", "weekday", "start_time", "end_time", "room", "is_active"]
    list_filter = ["is_active", "weekday", "room"]
    search_fields = ["group__name", "room__name"]
    list_select_related = ["group", "room"]


@admin.register(Lesson)
class LessonAdmin(admin.ModelAdmin):
    list_display = ["group", "date", "start_time", "end_time", "room", "teacher", "status"]
    list_filter = ["status", "date", "room"]
    search_fields = ["group__name", "teacher__full_name", "room__name"]
    list_select_related = ["group", "room", "teacher"]
    date_hierarchy = "date"
