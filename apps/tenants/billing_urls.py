from django.urls import path

from .billing_views import BillingView, SuggestView

app_name = "billing"

urlpatterns = [
    path("", BillingView.as_view(), name="overview"),
    path("suggest/", SuggestView.as_view(), name="suggest"),
]
