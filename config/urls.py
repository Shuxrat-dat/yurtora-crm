"""Root URL configuration for yurtora-crm project."""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("apps.accounts.urls", namespace="accounts")),
    path("students/", include("apps.students.urls", namespace="students")),
    path("teachers/", include("apps.teachers.urls", namespace="teachers")),
    path("courses/", include("apps.courses.urls", namespace="courses")),
    path("groups/", include("apps.groups.urls", namespace="groups")),
    path("schedule/", include("apps.schedule.urls", namespace="schedule")),
    path("audit/", include("apps.core.urls", namespace="core")),
    path("goals/", include("apps.core.goal_urls", namespace="goals")),
    path("notifications/", include("apps.notifications.urls", namespace="notifications")),
    path("finance/", include("apps.finance.urls", namespace="finance")),
    path("billing/", include("apps.tenants.billing_urls", namespace="billing")),
]

