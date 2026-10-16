from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.AuditLogListView.as_view(), name="audit_log"),
]
