from django.urls import path

from .billing_views import BillingView

app_name = "billing"

urlpatterns = [
    path("", BillingView.as_view(), name="overview"),
]
