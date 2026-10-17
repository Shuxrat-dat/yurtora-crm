"""URL patterns for accounts app."""

from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("staff/", views.StaffListView.as_view(), name="staff_list"),
    path("staff/create/", views.StaffCreateView.as_view(), name="staff_create"),
    path("staff/<int:pk>/", views.StaffUpdateView.as_view(), name="staff_edit"),
    path("staff/<int:pk>/password/", views.StaffPasswordView.as_view(), name="staff_password"),
    path("password/", views.SelfPasswordChangeView.as_view(), name="password_change"),
    path("login/", views.LoginView.as_view(), name="login"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    path("", views.HomeView.as_view(), name="home"),
    path("test/admin/", views.AdminTestView.as_view(), name="admin_test"),
    path("test/teacher/", views.TeacherTestView.as_view(), name="teacher_test"),
    path(
        "dashboard/schedule-grid/",
        views.DashboardScheduleGridView.as_view(),
        name="dashboard_schedule_grid",
    ),
    path("search/", views.GlobalSearchView.as_view(), name="global_search"),
    path("alerts/", views.AlertsView.as_view(), name="alerts"),
    path(
        "dashboard/daily-stats/",
        views.DailyStatsView.as_view(),
        name="daily_stats",
    ),
]
