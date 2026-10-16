"""URL configuration for students app."""

from django.urls import path

from apps.students import views

app_name = "students"

urlpatterns = [
    path("", views.StudentListView.as_view(), name="student_list"),
    path("churn/", views.ChurnReportView.as_view(), name="churn_report"),
    path("import/", views.StudentImportView.as_view(), name="student_import"),
    path(
        "import/template/",
        views.StudentImportTemplateView.as_view(),
        name="student_import_template",
    ),
    path("create/", views.StudentCreateView.as_view(), name="student_create"),
    path("<int:pk>/", views.StudentDetailView.as_view(), name="student_detail"),
    path("<int:pk>/edit/", views.StudentUpdateView.as_view(), name="student_edit"),
    path(
        "<int:pk>/archive/",
        views.StudentArchiveView.as_view(),
        name="student_archive",
    ),
    path(
        "<int:pk>/status/",
        views.StudentStatusView.as_view(),
        name="student_status",
    ),
    path(
        "<int:pk>/guardians/add/",
        views.GuardianCreateView.as_view(),
        name="guardian_add",
    ),
]
