"""URL configuration for schedule app."""

from django.urls import path

from apps.schedule import views

app_name = "schedule"

urlpatterns = [
    path("", views.WeeklyScheduleView.as_view(), name="weekly_schedule"),
    path("generate/", views.GenerateLessonsView.as_view(), name="generate_lessons"),
    path("lessons/<int:pk>/", views.LessonDetailView.as_view(), name="lesson_detail"),
    path("lessons/<int:pk>/mark-held/", views.MarkLessonHeldView.as_view(), name="mark_lesson_held"),
    path("lessons/<int:pk>/mark/", views.MarkAttendanceView.as_view(), name="mark_attendance"),
    path("lessons/<int:pk>/cancel/", views.CancelLessonView.as_view(), name="cancel_lesson"),
    path("lessons/<int:pk>/reschedule/", views.RescheduleLessonView.as_view(), name="reschedule_lesson"),
    path("rooms/", views.RoomListView.as_view(), name="room_list"),
    path("rooms/create/", views.RoomCreateView.as_view(), name="room_create"),
    path("rooms/<int:pk>/edit/", views.RoomUpdateView.as_view(), name="room_edit"),
    path("slots/", views.SlotListView.as_view(), name="slot_list"),
    path("slots/create/", views.SlotCreateView.as_view(), name="slot_create"),
    path("slots/<int:pk>/edit/", views.SlotUpdateView.as_view(), name="slot_edit"),
]
