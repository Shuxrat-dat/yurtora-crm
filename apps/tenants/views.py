"""Yurtora ommaviy sahifalari: bosh sahifa, markazni ro'yxatdan o'tkazish, texnik endpointlar."""

import logging

from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.http import HttpResponse
from django.shortcuts import redirect
from django.views.generic import FormView, TemplateView

from .forms import CenterSignupForm
from .models import Domain
from .services import create_center, public_hosts

logger = logging.getLogger(__name__)
SIGNUPS_PER_IP_PER_HOUR = 5  # sxema yaratish og'ir amal: suiiste'mol qilinmasin


def client_ip(request) -> str:
    """Caddy orqasida: X-Forwarded-For'ni Caddy o'zi qo'yadi (mijoz yuborgan qiymatni almashtiradi)."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR", "")


class LandingView(TemplateView):
    template_name = "public/landing.html"


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
