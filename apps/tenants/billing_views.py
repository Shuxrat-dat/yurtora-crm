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


FIELD_CSS = "w-full px-3 py-2 border border-slate-300 rounded-lg"


class PayRequestForm(forms.Form):
    plan = forms.ModelChoiceField(queryset=Plan.objects.none(), label="Tarif", empty_label=None, widget=forms.RadioSelect)
    months = forms.TypedChoiceField(
        label="Necha oyga", coerce=int, choices=[(m, f"{m} oy") for m in billing.MONTH_CHOICES], initial=1,
        widget=forms.RadioSelect,
    )
    payer_name = forms.CharField(label="To'lovchi (F.I.Sh.)", max_length=120, widget=forms.TextInput(attrs={"class": FIELD_CSS}))
    payer_phone = forms.CharField(label="Telefon", max_length=30, widget=forms.TextInput(attrs={"class": FIELD_CSS, "placeholder": "+998 90 123 45 67"}))
    note = forms.CharField(
        label="Izoh (chek raqami, karta oxirgi 4 raqami, to'lov vaqti)", max_length=255, required=False,
        widget=forms.TextInput(attrs={"class": FIELD_CSS}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["plan"].queryset = Plan.objects.filter(is_active=True)


class PayView(RoleRequiredMixin, FormView):
    """Direktor obunani to'laydi: rekvizitlar, tarif va muddat tanlovi, "To'lov qildim" so'rovi.

    Obuna tugab markaz o'chgan bo'lsa ham ochiq (middleware EXPIRED_ALLOWED_PATHS). Egasi panelda tasdiqlaydi.
    """

    template_name = "tenants/pay.html"
    form_class = PayRequestForm
    allowed_roles = ()  # faqat direktor

    def get_initial(self):
        center = self.request.tenant
        user = self.request.user
        return {
            "plan": center.plan_id or Plan.objects.filter(is_active=True, is_default=True).values_list("pk", flat=True).first(),
            "months": 1,
            "payer_name": user.get_full_name() or "",
            "payer_phone": center.contact_phone,
        }

    def get_context_data(self, **kwargs):
        from .models import PaymentRequest, PlatformSettings

        context = super().get_context_data(**kwargs)
        center = self.request.tenant
        platform = PlatformSettings.get()
        with schema_context(get_public_schema_name()):
            requests = list(PaymentRequest.objects.filter(center_id=center.pk).select_related("plan")[:5])
        context.update(
            center=center,
            sub=getattr(self.request, "subscription", None) or billing.subscription(center),
            plans=Plan.objects.filter(is_active=True),
            payment_details=platform.payment_details,
            support=platform.support_contact or settings.YURTORA_SUPPORT,
            requests=requests,
            pending=next((r for r in requests if r.status == PaymentRequest.Status.NEW), None),
        )
        return context

    def form_valid(self, form):
        from django.core.exceptions import ValidationError

        center = self.request.tenant
        data = form.cleaned_data
        try:
            with schema_context(get_public_schema_name()):
                billing.create_payment_request(
                    center, plan=data["plan"], months=data["months"], payer_name=data["payer_name"],
                    payer_phone=data["payer_phone"], note=data["note"],
                )
        except ValidationError as exc:
            form.add_error(None, exc.messages[0])
            return self.form_invalid(form)
        messages.success(self.request, "So'rov yuborildi. Yurtora to'lovni tasdiqlashi bilan obuna avtomatik uzayadi.")
        return redirect("billing:pay")
