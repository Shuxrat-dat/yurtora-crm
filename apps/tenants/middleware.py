"""Manzil bo'yicha markazni tanlash: markaz sxemasi, Yurtora sayti yoki 404."""

from django.conf import settings
from django.db import connection
from django.http import HttpResponse, HttpResponseNotFound
from django.template.loader import render_to_string
from django_tenants.middleware.main import TenantMainMiddleware

from . import billing
from .services import public_hosts

# Har bir sahifa so'rovi uchun doimiy qo'shimcha SQL: search_path=public, manzil bo'yicha markaz,
# search_path=markaz (TENANT_LIMIT_SET_CALLS bilan). Ma'lumotlar hajmiga bog'liq emas.
TENANCY_QUERIES_PER_REQUEST = 3

# Caddy va healthcheck ichki manzildan (web:8000) keladi: bu yo'llar har doim ommaviy qismda
ALWAYS_PUBLIC_PATHS = ("/_tls/check/", "/healthz/")
# Faqat ko'rish rejimida ham ruxsat: tizimga kirish/chiqish
READ_ONLY_ALLOWED_PATHS = ("/login/", "/logout/")
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


class CenterMiddleware(TenantMainMiddleware):
    def process_request(self, request):
        if request.path in ALWAYS_PUBLIC_PATHS:
            connection.set_schema_to_public()
            self.setup_url_routing(request, force_public=True)
            return None
        response = super().process_request(request)
        tenant = getattr(request, "tenant", None)
        if response is not None or tenant is None:
            return response
        sub = billing.subscription(tenant)
        request.subscription = sub
        if sub.status == billing.BLOCKED:
            # Markaz bloklangan (qo'lda yoki obuna muddati o'tgan): ma'lumotlar saqlanadi, kirish yo'q
            connection.set_schema_to_public()
            html = render_to_string(
                "public/center_blocked.html", {"center": tenant, "sub": sub, "YURTORA_SUPPORT": settings.YURTORA_SUPPORT}
            )
            return HttpResponse(html, status=403)
        if sub.status == billing.READ_ONLY and request.method not in SAFE_METHODS and request.path not in READ_ONLY_ALLOWED_PATHS:
            html = render_to_string("public/center_read_only.html", {"center": tenant, "sub": sub})
            return HttpResponse(html, status=403)
        return None

    def no_tenant_found(self, request, hostname):
        if hostname in public_hosts():
            self.setup_url_routing(request, force_public=True)
            return None
        html = render_to_string("public/center_not_found.html", {"hostname": hostname})
        return HttpResponseNotFound(html)
