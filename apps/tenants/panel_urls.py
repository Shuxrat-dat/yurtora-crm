"""Yurtora egasi paneli: crm.yurtora.uz/panel/ (faqat Yurtora xodimlari)."""

from django.urls import path

from . import panel_views as views

app_name = "panel"

urlpatterns = [
    path("", views.CentersView.as_view(), name="centers"),
    path("centers/<int:pk>/", views.CenterDetailView.as_view(), name="center"),
    path("requests/", views.RequestsView.as_view(), name="requests"),
    path("plans/", views.PlansView.as_view(), name="plans"),
    path("plans/<int:pk>/", views.PlansView.as_view(), name="plan_edit"),
    path("settings/", views.SettingsView.as_view(), name="settings"),
]
