from django.core.paginator import Paginator
from django.views.generic import TemplateView

from apps.accounts.permissions import RoleRequiredMixin

from .models import AuditLog


class AuditLogListView(RoleRequiredMixin, TemplateView):
    """Harakatlar jurnali: faqat direktor (direktorga hamma joyga ruxsat beriladi)."""

    allowed_roles = ()
    template_name = "core/audit_list.html"
    paginate_by = 50

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        qs = AuditLog.objects.all()
        params = self.request.GET

        username = params.get("user", "").strip()
        action = params.get("action", "")
        model = params.get("model", "")
        if username:
            qs = qs.filter(username__icontains=username)
        if action in AuditLog.Action.values:
            qs = qs.filter(action=action)
        if model:
            qs = qs.filter(model=model)

        page = Paginator(qs, self.paginate_by).get_page(params.get("page"))
        query = params.copy()
        query.pop("page", None)
        ctx.update(
            page_obj=page,
            actions=AuditLog.Action.choices,
            models=AuditLog.objects.order_by().values_list("model", flat=True).distinct(),
            filters={"user": username, "action": action, "model": model},
            querystring=query.urlencode(),
        )
        return ctx
