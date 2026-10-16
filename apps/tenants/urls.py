from django.urls import path

from . import views

app_name = "public"

urlpatterns = [
    path("", views.LandingView.as_view(), name="landing"),
    path("signup/", views.SignupView.as_view(), name="signup"),
]
