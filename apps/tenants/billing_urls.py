from django.urls import path

from .billing_views import BillingView, PayView, SuggestView

app_name = "billing"

urlpatterns = [
    path("", BillingView.as_view(), name="overview"),
    path("pay/", PayView.as_view(), name="pay"),
    path("suggest/", SuggestView.as_view(), name="suggest"),
]
