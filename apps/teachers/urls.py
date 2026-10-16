"""URL configuration for teachers app."""

from django.urls import path

from apps.teachers import views

app_name = "teachers"

urlpatterns = [
    path("", views.TeacherListView.as_view(), name="teacher_list"),
    path("create/", views.TeacherCreateView.as_view(), name="teacher_create"),
    path("me/", views.TeacherProfileView.as_view(), name="my_profile"),
    path("<int:pk>/", views.TeacherProfileView.as_view(), name="teacher_profile"),
    path("<int:pk>/edit/", views.TeacherUpdateView.as_view(), name="teacher_edit"),
]
