from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    path("debtors/", views.DebtReminderView.as_view(), name="debt_reminders"),
    path("admin-chat/", views.AdminChatView.as_view(), name="admin_chat"),
    path("student/<int:pk>/", views.StudentTelegramView.as_view(), name="student_telegram"),
]
