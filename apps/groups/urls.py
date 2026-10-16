"""URL configuration for groups app."""

from django.urls import path

from apps.groups import views

app_name = "groups"

urlpatterns = [
    path("", views.GroupListView.as_view(), name="group_list"),
    path("create/", views.GroupCreateView.as_view(), name="group_create"),
    path("<int:pk>/", views.GroupDetailView.as_view(), name="group_detail"),
    path("<int:pk>/edit/", views.GroupUpdateView.as_view(), name="group_edit"),
    path("<int:pk>/attendance/", views.GroupAttendanceView.as_view(), name="group_attendance"),
    path("<int:pk>/enroll/", views.GroupEnrollView.as_view(), name="group_enroll"),
    path(
        "<int:pk>/convert-trial/<int:enrollment_id>/",
        views.GroupTrialConvertView.as_view(),
        name="group_trial_convert",
    ),
    path(
        "<int:pk>/freeze/<int:enrollment_id>/",
        views.GroupFreezeView.as_view(),
        name="group_freeze",
    ),
    path(
        "<int:pk>/unfreeze/<int:enrollment_id>/",
        views.GroupUnfreezeView.as_view(),
        name="group_unfreeze",
    ),
    path(
        "<int:pk>/transfer/<int:student_id>/",
        views.GroupTransferView.as_view(),
        name="group_transfer",
    ),
    path(
        "<int:pk>/remove/<int:student_id>/",
        views.GroupRemoveView.as_view(),
        name="group_remove",
    ),
]
