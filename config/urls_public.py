"""Yurtora ommaviy qismi (yurtora.uz): bosh sahifa, ro'yxatdan o'tish, Yurtora xodimlari uchun admin."""

from django.contrib import admin
from django.urls import include, path

from apps.tenants import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz/", views.healthz),
    path("_tls/check/", views.tls_check),
    path("", include("apps.tenants.urls", namespace="public")),
]
