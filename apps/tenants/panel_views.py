"""Yurtora egasi paneli (crm.yurtora.uz/panel/): markazlar, tarif berish, obunani uzaytirish yoki o'chirish,
markazlardan kelgan to'lov so'rovlarini tasdiqlash, tariflar va to'lov rekvizitlari.

Faqat Yurtora xodimlari (public sxemadagi is_staff foydalanuvchilar, Django admin bilan bir xil kirish).
"""

from django import forms
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django_tenants.utils import get_public_schema_name, schema_context

from . import billing
from .models import Center, PaymentRequest, Plan, PlatformSettings, SubscriptionPayment

FIELD = "w-full px-3 py-2 border border-slate-300 rounded-lg"

STATUS_TONE = {
    billing.TRIAL: "cyan", billing.ACTIVE: "ok", billing.GRACE: "gold", billing.READ_ONLY: "gold", billing.BLOCKED: "red",
}


def staff_only(view):
    return method_decorator(staff_member_required(login_url="admin:login"), name="dispatch")(view)


def _expiring(sub, days=7):
    return sub.full_access and sub.days_left is not None and sub.days_left <= days


@staff_only
class CentersView(View):
    def get(self, request):
        today = timezone.localdate()
        qs = Center.objects.exclude(schema_name=get_public_schema_name()).select_related("plan").order_by("name")
        q = request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(schema_name__icontains=q) | Q(contact_phone__icontains=q))
        pending = set(PaymentRequest.objects.filter(status=PaymentRequest.Status.NEW).values_list("center_id", flat=True))
        all_rows = []
        for center in qs:
            sub = billing.subscription(center, today)
            all_rows.append({"center": center, "sub": sub, "tone": STATUS_TONE[sub.status], "pending": center.pk in pending})
        status = request.GET.get("status", "")
        if status == "expiring":
            rows = [r for r in all_rows if _expiring(r["sub"])]
        elif status:
            rows = [r for r in all_rows if r["sub"].status == status]
        else:
            rows = all_rows
        stats = {
            "total": len(all_rows),
            "working": sum(r["sub"].full_access for r in all_rows),
            "expiring": sum(_expiring(r["sub"]) for r in all_rows),
            "off": sum(not r["sub"].full_access for r in all_rows),
            "mrr": sum(r["center"].plan.price_month for r in all_rows if r["sub"].status == billing.ACTIVE and r["center"].plan),
            "pending": len(pending),
        }
        return render(request, "panel/centers.html", {
            "rows": rows, "stats": stats, "q": q, "status": status, "statuses": billing.STATUS_LABELS, "nav": "centers",
        })


class GrantForm(forms.Form):
    plan = forms.ModelChoiceField(queryset=Plan.objects.all(), label="Tarif", widget=forms.Select(attrs={"class": FIELD}))
    months = forms.IntegerField(label="Oylar", min_value=1, max_value=36, initial=1, widget=forms.NumberInput(attrs={"class": FIELD}))
    amount = forms.IntegerField(
        label="Summa (so'm)", min_value=0, required=False, widget=forms.NumberInput(attrs={"class": FIELD}),
        help_text="Bo'sh — tarif narxi × oylar. 0 — bepul (bonus).",
    )
    method = forms.ChoiceField(choices=SubscriptionPayment.Method.choices, label="Usul", widget=forms.Select(attrs={"class": FIELD}))
    comment = forms.CharField(label="Izoh", max_length=255, required=False, widget=forms.TextInput(attrs={"class": FIELD}))


class TrialForm(forms.Form):
    trial_ends_at = forms.DateField(label="Sinov davri tugaydi", widget=forms.DateInput(attrs={"type": "date", "class": FIELD}, format="%Y-%m-%d"))


class PlanChangeForm(forms.Form):
    plan = forms.ModelChoiceField(
        queryset=Plan.objects.all(), label="Tarif", required=False, empty_label="— tarifsiz (cheklovsiz) —",
        widget=forms.Select(attrs={"class": FIELD}),
    )


@staff_only
class CenterDetailView(View):
    template_name = "panel/center_detail.html"

    def _context(self, center, **bound):
        sub = billing.subscription(center)
        with schema_context(center.schema_name):
            from apps.accounts.models import User
            from apps.students.models import Student

            usage = {
                "students": Student.objects.exclude(status=Student.Status.ARCHIVED).count(),
                "users": User.objects.filter(is_active=True).count(),
            }
        return {
            "center": center, "sub": sub, "tone": STATUS_TONE[sub.status], "usage": usage, "nav": "centers",
            "payments": center.payments.select_related("plan", "created_by")[:30],
            "requests": center.payment_requests.select_related("plan")[:20],
            "grant_form": bound.get("grant_form") or GrantForm(initial={"plan": center.plan_id}),
            "trial_form": bound.get("trial_form") or TrialForm(initial={"trial_ends_at": center.trial_ends_at}),
            "plan_form": PlanChangeForm(initial={"plan": center.plan_id}),
            "domain": center.primary_domain,
        }

    def get(self, request, pk):
        center = get_object_or_404(Center.objects.select_related("plan"), pk=pk)
        return render(request, self.template_name, self._context(center))

    def post(self, request, pk):
        center = get_object_or_404(Center, pk=pk)
        action = request.POST.get("action")
        try:
            if action == "grant":
                form = GrantForm(request.POST)
                if not form.is_valid():
                    return render(request, self.template_name, self._context(center, grant_form=form))
                data = form.cleaned_data
                amount = data["amount"] if data["amount"] is not None else data["plan"].price_month * data["months"]
                payment = billing.record_payment(
                    center, months=data["months"], amount=amount, method=data["method"], plan=data["plan"],
                    comment=data["comment"], user=request.user,
                )
                messages.success(request, f"Obuna {payment.period_end:%d.%m.%Y} gacha uzaytirildi ({data['plan'].name}).")
            elif action == "plan":
                form = PlanChangeForm(request.POST)
                if form.is_valid():
                    center.plan = form.cleaned_data["plan"]
                    center.save(update_fields=["plan"])
                    messages.success(request, "Tarif o'zgartirildi (muddat o'zgarmadi).")
            elif action == "trial":
                form = TrialForm(request.POST)
                if not form.is_valid():
                    return render(request, self.template_name, self._context(center, trial_form=form))
                center.trial_ends_at = form.cleaned_data["trial_ends_at"]
                center.save(update_fields=["trial_ends_at"])
                messages.success(request, f"Sinov davri {center.trial_ends_at:%d.%m.%Y} gacha.")
            elif action == "block":
                center.is_active = False
                center.save(update_fields=["is_active"])
                messages.success(request, "Markaz to'xtatildi: kirish yopiq, ma'lumotlar saqlanadi.")
            elif action == "unblock":
                center.is_active = True
                center.save(update_fields=["is_active"])
                messages.success(request, "Markaz qayta yoqildi (obuna muddati amal qiladi).")
            elif action == "end":
                billing.end_subscription_now(center)
                messages.success(request, "Obuna tugatildi: markazda tushuntirish oynasi va to'lov sahifasi ochiq.")
            elif action in ("confirm", "reject"):
                _resolve_request(request, center, action)
        except ValidationError as exc:
            messages.error(request, exc.messages[0])
        return redirect("panel:center", pk=center.pk)


def _resolve_request(request, center, action):
    req = get_object_or_404(PaymentRequest, pk=request.POST.get("request_id"), center=center)
    if action == "confirm":
        payment = billing.confirm_payment_request(req, user=request.user)
        messages.success(request, f"To'lov tasdiqlandi: «{center.name}» obunasi {payment.period_end:%d.%m.%Y} gacha.")
    else:
        billing.reject_payment_request(req, reason=request.POST.get("reason", ""))
        messages.success(request, "So'rov rad etildi.")


@staff_only
class RequestsView(View):
    def get(self, request):
        status = request.GET.get("status", PaymentRequest.Status.NEW)
        qs = PaymentRequest.objects.select_related("center", "plan")
        if status:
            qs = qs.filter(status=status)
        return render(request, "panel/requests.html", {
            "requests": qs[:100], "status": status, "statuses": PaymentRequest.Status.choices, "nav": "requests",
        })

    def post(self, request):
        req = get_object_or_404(PaymentRequest, pk=request.POST.get("request_id"))
        try:
            _resolve_request(request, req.center, request.POST.get("action"))
        except ValidationError as exc:
            messages.error(request, exc.messages[0])
        return redirect("panel:requests")


class PlanForm(forms.ModelForm):
    class Meta:
        model = Plan
        fields = ("name", "code", "price_month", "max_students", "max_users", "is_default", "is_active", "sort")
        widgets = {
            "name": forms.TextInput(attrs={"class": FIELD}),
            "code": forms.TextInput(attrs={"class": FIELD}),
            "price_month": forms.NumberInput(attrs={"class": FIELD}),
            "max_students": forms.NumberInput(attrs={"class": FIELD}),
            "max_users": forms.NumberInput(attrs={"class": FIELD}),
            "sort": forms.NumberInput(attrs={"class": FIELD}),
        }


@staff_only
class PlansView(View):
    def _render(self, request, form, plan):
        plans = Plan.objects.annotate(center_count=Count("centers"))
        return render(request, "panel/plans.html", {"plans": plans, "form": form, "plan": plan, "nav": "plans"})

    def get(self, request, pk=None):
        plan = get_object_or_404(Plan, pk=pk) if pk else None
        return self._render(request, PlanForm(instance=plan), plan)

    def post(self, request, pk=None):
        plan = get_object_or_404(Plan, pk=pk) if pk else None
        form = PlanForm(request.POST, instance=plan)
        if not form.is_valid():
            return self._render(request, form, plan)
        saved = form.save()
        if saved.is_default:
            Plan.objects.exclude(pk=saved.pk).update(is_default=False)  # yangi markazlar uchun faqat bitta tarif
        messages.success(request, f"«{saved.name}» saqlandi.")
        return redirect("panel:plans")


class SettingsForm(forms.ModelForm):
    class Meta:
        model = PlatformSettings
        fields = ("payment_details", "support_contact", "grace_days", "read_only_days")
        widgets = {
            "payment_details": forms.Textarea(attrs={"class": FIELD, "rows": 5, "placeholder": "Karta: 8600 ...\nQabul qiluvchi: ...\nBank: ..."}),
            "support_contact": forms.TextInput(attrs={"class": FIELD}),
            "grace_days": forms.NumberInput(attrs={"class": FIELD}),
            "read_only_days": forms.NumberInput(attrs={"class": FIELD}),
        }


@staff_only
class SettingsView(View):
    def _instance(self):
        return PlatformSettings.objects.filter(pk=1).first() or PlatformSettings(pk=1)

    def get(self, request):
        return render(request, "panel/settings.html", {"form": SettingsForm(instance=self._instance()), "nav": "settings"})

    def post(self, request):
        form = SettingsForm(request.POST, instance=self._instance())
        if not form.is_valid():
            return render(request, "panel/settings.html", {"form": form, "nav": "settings"})
        form.save()
        messages.success(request, "Sozlamalar saqlandi.")
        return redirect("panel:settings")
