"""Manzil bo'yicha markazni tanlash: markaz sxemasi, Yurtora sayti yoki 404."""

from django.db import connection
from django.http import HttpResponse, HttpResponseNotFound
from django.template.loader import render_to_string
from django_tenants.middleware.main import TenantMainMiddleware

from .services import public_hosts

# Har bir sahifa so'rovi uchun doimiy qo'shimcha SQL: search_path=public, manzil bo'yicha markaz,
# search_path=markaz (TENANT_LIMIT_SET_CALLS bilan). Ma'lumotlar hajmiga bog'liq emas.
TENANCY_QUERIES_PER_REQUEST = 3

# Caddy va healthcheck ichki manzildan (web:8000) keladi: bu yo'llar har doim ommaviy qismda
ALWAYS_PUBLIC_PATHS = ("/_tls/check/", "/healthz/")


class CenterMiddleware(TenantMainMiddleware):
    def process_request(self, request):
        if request.path in ALWAYS_PUBLIC_PATHS:
            connection.set_schema_to_public()
            self.setup_url_routing(request, force_public=True)
            return None
        response = super().process_request(request)
        tenant = getattr(request, "tenant", None)
        if response is None and tenant is not None and not tenant.is_active:
            # Markaz bloklangan: ma'lumotlar saqlanadi, lekin kirish yo'q
            connection.set_schema_to_public()
            html = render_to_string("public/center_blocked.html", {"center": tenant})
            return HttpResponse(html, status=403)
        return response

    def no_tenant_found(self, request, hostname):
        if hostname in public_hosts():
            self.setup_url_routing(request, force_public=True)
            return None
        html = render_to_string("public/center_not_found.html", {"hostname": hostname})
        return HttpResponseNotFound(html)
