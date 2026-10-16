"""Markaz direktori uchun "Obuna" sahifasi (markaz manzilida: /billing/)."""

from django.conf import settings
from django.views.generic import TemplateView

from apps.accounts.permissions import RoleRequiredMixin

from . import billing
from .models import Plan


class BillingView(RoleRequiredMixin, TemplateView):
    template_name = "tenants/billing.html"
    allowed_roles = ()  # faqat direktor

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        center = self.request.tenant
        context.update(
            center=center,
            sub=getattr(self.request, "subscription", None) or billing.subscription(center),
            usage=billing.usage(),
            plans=Plan.objects.filter(is_active=True),
            payments=center.payments.select_related("plan")[:24],
            support=settings.YURTORA_SUPPORT,
        )
        return context
