"""Yurtora ommaviy sahifalari: bosh sahifa, markazni ro'yxatdan o'tkazish, texnik endpointlar."""

import logging

from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.http import HttpResponse
from django.shortcuts import redirect
from django.views.generic import FormView, TemplateView

from .forms import CenterSignupForm, DemoRequestForm
from .models import Domain
from .services import create_center, public_hosts

logger = logging.getLogger(__name__)
SIGNUPS_PER_IP_PER_HOUR = 5  # sxema yaratish og'ir amal: suiiste'mol qilinmasin


def client_ip(request) -> str:
    """Caddy orqasida: X-Forwarded-For'ni Caddy o'zi qo'yadi (mijoz yuborgan qiymatni almashtiradi)."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR", "")


DEMOS_PER_IP_PER_HOUR = 3


class LandingView(FormView):
    """Reklama lendingi: imkoniyatlar, narxlar va "Demo buyurtma" formasi (shu sahifaning o'zida)."""

    template_name = "public/landing.html"
    form_class = DemoRequestForm

    def get_initial(self):
        return {"plan": self.request.GET.get("plan", "")}

    def get_context_data(self, **kwargs):
        from django.conf import settings

        from .models import Plan

        context = super().get_context_data(**kwargs)
        context["plans"] = list(Plan.objects.filter(is_active=True))
        context["support"] = settings.YURTORA_SUPPORT
        return context

    def form_valid(self, form):
        from datetime import timedelta

        from django.utils import timezone

        from .models import DemoRequest
        from .notify import notify_owner

        data = form.cleaned_data
        if data["website"]:  # spam tuzog'i to'ldirilgan: botga muvaffaqiyat ko'rsatamiz, saqlamaymiz
            return redirect("public:demo_thanks")
        ip = client_ip(self.request) or None
        now = timezone.now()
        if ip and DemoRequest.objects.filter(ip_address=ip, created_at__gte=now - timedelta(hours=1)).count() >= DEMOS_PER_IP_PER_HOUR:
            form.add_error(None, "Juda ko'p buyurtma yuborildi. Birozdan keyin urinib ko'ring yoki bizga qo'ng'iroq qiling.")
            return self.form_invalid(form)
        digits = "".join(ch for ch in data["phone"] if ch.isdigit())[-9:]
        recent = DemoRequest.objects.filter(created_at__gte=now - timedelta(hours=24)).values_list("phone", flat=True)
        if any("".join(c for c in p if c.isdigit())[-9:] == digits for p in recent):
            return redirect("public:demo_thanks")  # bir xil telefondan takror: bitta buyurtma yetarli
        demo = DemoRequest.objects.create(
            name=data["name"].strip(),
            center_name=data["center_name"].strip(),
            phone=data["phone"],
            students=data["students"],
            plan=data["plan"],
            message=data["message"].strip(),
            ip_address=ip,
        )
        logger.info("Yangi demo buyurtma: %s", demo.center_name)
        notify_owner(
            "Yangi demo buyurtma\n"
            f"Markaz: {demo.center_name}\nIsm: {demo.name}\nTelefon: {demo.phone}\n"
            f"O'quvchilar: {demo.get_students_display()}\n"
            + (f"Tarif: {demo.plan}\n" if demo.plan else "")
            + (f"Izoh: {demo.message}" if demo.message else "")
        )
        return redirect("public:demo_thanks")


class DemoThanksView(TemplateView):
    template_name = "public/demo_thanks.html"

    def get_context_data(self, **kwargs):
        from django.conf import settings

        context = super().get_context_data(**kwargs)
        context["support"] = settings.YURTORA_SUPPORT
        return context


class SignupView(FormView):
    template_name = "public/signup.html"
    form_class = CenterSignupForm

    def form_valid(self, form):
        key = f"signup:{client_ip(self.request)}"
        attempts = cache.get(key, 0)
        if attempts >= SIGNUPS_PER_IP_PER_HOUR:
            form.add_error(None, "Juda ko'p urinish. Bir soatdan keyin qayta urinib ko'ring yoki bizga yozing.")
            return self.form_invalid(form)
        cache.set(key, attempts + 1, 3600)
        data = form.cleaned_data
        center = create_center(
            name=data["name"],
            subdomain=data["subdomain"],
            director_username=data["username"],
            director_password=data["password1"],
            contact_name=data["contact_name"],
            contact_phone=data["contact_phone"],
        )
        logger.info("Yangi markaz ro'yxatdan o'tdi: %s", center.schema_name)
        messages.success(self.request, "Markaz yaratildi. Endi direktor logini bilan kiring.")
        return redirect(center_login_url(self.request, center.primary_domain))


def center_login_url(request, domain: str) -> str:
    port = request.get_port()
    suffix = "" if port in ("80", "443") else f":{port}"
    return f"{request.scheme}://{domain}{suffix}/login/?welcome=1"


def healthz(request):
    return HttpResponse("ok", content_type="text/plain")


def tls_check(request):
    """Caddy on-demand TLS: sertifikat faqat Yurtora va ro'yxatdan o'tgan faol markazlar manzillari uchun."""
    domain = (request.GET.get("domain") or "").strip().lower()
    allowed = domain in public_hosts() or Domain.objects.filter(domain=domain, tenant__is_active=True).exists()
    return HttpResponse(status=200 if allowed else 404)


def root_domain(request):
    """Shablonlar uchun: ommaviy sahifalarda manzil namunasi (ilm.yurtora.uz)."""
    return {"ROOT_DOMAIN": settings.ROOT_DOMAIN}


BANNER_ROLES = ("director", "administrator", "accountant")


def subscription_banner(request):
    """CRM yuqorisida obuna haqida ogohlantirish (direktor, administrator, buxgalter uchun)."""
    from . import billing

    sub = getattr(request, "subscription", None)
    user = getattr(request, "user", None)
    if sub is None or user is None or not user.is_authenticated or getattr(user, "role", "") not in BANNER_ROLES:
        return {}
    days = sub.days_left
    banner = None
    if sub.status == billing.TRIAL and days is not None and days <= 7:
        banner = ("info", f"Sinov davri: {days + 1} kun qoldi ({sub.access_until:%d.%m.%Y} gacha). Obunani oldindan to'lasangiz, sinov kunlari yo'qolmaydi.")
    elif sub.status == billing.ACTIVE and days is not None and days <= 5:
        banner = ("info", f"Obuna {sub.access_until:%d.%m.%Y} da tugaydi ({days + 1} kun qoldi).")
    elif sub.status == billing.GRACE:
        banner = ("warning", f"Obuna muddati {sub.access_until:%d.%m.%Y} da tugadi. {sub.read_only_from:%d.%m.%Y} dan faqat ko'rish rejimi yoqiladi.")
    elif sub.status == billing.READ_ONLY:
        banner = ("error", f"Faqat ko'rish rejimi: o'zgartirishlar obuna to'langach ochiladi. {sub.blocked_from:%d.%m.%Y} dan kirish to'xtatiladi.")
    return {"SUBSCRIPTION_BANNER": banner} if banner else {}
