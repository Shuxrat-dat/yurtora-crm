from django import forms
from django.contrib import admin, messages

from . import billing
from .models import Center, DemoRequest, Domain, Plan, SubscriptionPayment, Suggestion


class DomainInline(admin.TabularInline):
    model = Domain
    extra = 0


class PaymentInline(admin.TabularInline):
    """Markaz kartasida to'lovlar tarixi (faqat ko'rish: to'lov "Obuna to'lovlari" bo'limida qo'shiladi)."""

    model = SubscriptionPayment
    extra = 0
    can_delete = False
    fields = ("created_at", "plan", "months", "amount", "method", "period_start", "period_end", "comment")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "price_month", "max_students", "max_users", "is_default", "is_active", "sort")
    list_editable = ("price_month", "is_active", "sort")


@admin.register(Center)
class CenterAdmin(admin.ModelAdmin):
    list_display = ("name", "schema_name", "plan", "subscription_status", "access_until", "contact_phone", "is_active", "created_at")
    list_filter = ("is_active", "plan")
    search_fields = ("name", "schema_name", "contact_name", "contact_phone")
    # paid_until faqat to'lov orqali o'zgaradi (tarixsiz qo'lda uzaytirish bo'lmasin); sinovni uzaytirish mumkin
    readonly_fields = ("schema_name", "created_at", "paid_until", "subscription_status")
    fields = ("name", "schema_name", "contact_name", "contact_phone", "plan", "trial_ends_at", "paid_until", "subscription_status", "is_active", "created_at")
    inlines = [DomainInline, PaymentInline]

    @admin.display(description="Obuna holati")
    def subscription_status(self, obj):
        return billing.subscription(obj).label if obj.pk else "—"

    @admin.display(description="Ruxsat (shu kungacha)")
    def access_until(self, obj):
        return billing.subscription(obj).access_until or "—"

    def has_delete_permission(self, request, obj=None):
        return False  # markazni o'chirish o'rniga bloklang (is_active)


class PaymentForm(forms.ModelForm):
    class Meta:
        model = SubscriptionPayment
        fields = ("center", "plan", "months", "amount", "method", "comment")


@admin.register(SubscriptionPayment)
class SubscriptionPaymentAdmin(admin.ModelAdmin):
    """To'lovni qo'shish = markaz muddatini uzaytirish. Qo'shilgan to'lov o'zgartirilmaydi va o'chirilmaydi."""

    form = PaymentForm
    list_display = ("center", "plan", "months", "amount", "method", "period_start", "period_end", "created_by", "created_at")
    list_filter = ("method", "plan")
    search_fields = ("center__name", "center__schema_name", "comment")
    readonly_fields = ("period_start", "period_end", "created_by", "created_at")

    def get_fields(self, request, obj=None):
        base = ("center", "plan", "months", "amount", "method", "comment")
        return base + self.readonly_fields if obj else base

    def has_change_permission(self, request, obj=None):
        return obj is None

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        data = form.cleaned_data
        payment = billing.record_payment(
            data["center"], months=data["months"], amount=data["amount"], method=data["method"],
            plan=data["plan"], comment=data.get("comment", ""), user=request.user,
        )
        # admin jurnali va yo'naltirish haqiqiy yozuv bilan ishlashi uchun
        for field in payment._meta.concrete_fields:
            setattr(obj, field.attname, getattr(payment, field.attname))
        obj._state.adding = False
        messages.success(request, f"{payment.center.name}: obuna {payment.period_end:%d.%m.%Y} gacha uzaytirildi.")


@admin.register(Suggestion)
class SuggestionAdmin(admin.ModelAdmin):
    list_display = ("created_at", "center", "author", "role", "short_text", "status")
    list_filter = ("status", "center")
    list_editable = ("status",)
    search_fields = ("text", "author", "center__name", "contact")
    readonly_fields = ("center", "author", "role", "text", "contact", "created_at")
    date_hierarchy = "created_at"

    @admin.display(description="Taklif")
    def short_text(self, obj):
        return obj.text[:90] + ("…" if len(obj.text) > 90 else "")

    def has_add_permission(self, request):
        return False  # takliflar faqat markazlardan keladi


@admin.register(DemoRequest)
class DemoRequestAdmin(admin.ModelAdmin):
    list_display = ("created_at", "center_name", "name", "phone", "students", "plan", "status")
    list_filter = ("status", "students", "plan")
    list_editable = ("status",)
    search_fields = ("center_name", "name", "phone", "message", "note")
    readonly_fields = ("name", "center_name", "phone", "students", "plan", "message", "ip_address", "created_at")
    fields = ("status", "note", "name", "center_name", "phone", "students", "plan", "message", "ip_address", "created_at")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False  # buyurtmalar faqat lendingdan keladi
