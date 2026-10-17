"""Markaz direktori uchun "Obuna" sahifasi (markaz manzilida: /billing/)."""

from datetime import timedelta

from django import forms
from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect
from django.utils import timezone
from django.views.generic import FormView, TemplateView
from django_tenants.utils import get_public_schema_name, schema_context

from apps.accounts.models import User
from apps.accounts.permissions import RoleRequiredMixin

from . import billing
from .models import Plan, Suggestion


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


SUGGEST_LIMIT_PER_HOUR = 5


class SuggestionForm(forms.Form):
    text = forms.CharField(
        label="Taklifingiz",
        min_length=10,
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 6, "placeholder": "Qanday imkoniyat yoki o'zgarish kerak? Nima noqulay?", "class": "w-full px-3 py-2 border border-slate-300 rounded-lg"}),
        error_messages={"min_length": "Taklif kamida 10 ta belgidan iborat bo'lsin."},
    )
    contact = forms.CharField(
        label="Aloqa uchun telefon yoki Telegram (ixtiyoriy)",
        max_length=60,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "+998 90 123 45 67", "class": "w-full px-3 py-2 border border-slate-300 rounded-lg"}),
    )


class SuggestView(RoleRequiredMixin, FormView):
    """Markaz xodimi (direktor/administrator) Yurtora egasiga taklif yuboradi."""

    template_name = "tenants/suggest.html"
    form_class = SuggestionForm
    allowed_roles = [User.Role.ADMINISTRATOR]  # direktor ham kiradi

    def form_valid(self, form):
        center = self.request.tenant
        with schema_context(get_public_schema_name()):
            since = timezone.now() - timedelta(hours=1)
            if Suggestion.objects.filter(center_id=center.pk, created_at__gte=since).count() >= SUGGEST_LIMIT_PER_HOUR:
                form.add_error(None, "Bir soatda juda ko'p taklif yuborildi. Birozdan keyin qayta urinib ko'ring.")
                return self.form_invalid(form)
            user = self.request.user
            Suggestion.objects.create(
                center_id=center.pk,
                author=(user.get_full_name() or user.username)[:150],
                role=user.get_role_display()[:30],
                text=form.cleaned_data["text"].strip(),
                contact=form.cleaned_data["contact"].strip(),
            )
        messages.success(self.request, "Rahmat! Taklifingiz Yurtora jamoasiga yuborildi.")
        return redirect("accounts:home")
