from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, ListView, TemplateView, UpdateView

from apps.accounts.permissions import RoleRequiredMixin
from apps.core.forms import GoalForm
from apps.core.goals import goal_progress
from apps.core.reports import reports_for

from .models import AuditLog, Goal


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


class GoalListView(RoleRequiredMixin, ListView):
    """Maqsadlar ro'yxati (faqat direktor)."""

    model = Goal
    template_name = "core/goal_list.html"
    allowed_roles = ()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["rows"] = [goal_progress(g) for g in context["object_list"]]
        return context


class GoalCreateView(RoleRequiredMixin, CreateView):
    model = Goal
    form_class = GoalForm
    template_name = "core/goal_form.html"
    allowed_roles = ()
    success_url = reverse_lazy("goals:goal_list")

    def get_initial(self):
        return {"is_main": not Goal.objects.filter(is_main=True).exists()}

    def form_valid(self, form):
        messages.success(self.request, "Maqsad qo'shildi.")
        return super().form_valid(form)


class GoalUpdateView(RoleRequiredMixin, UpdateView):
    model = Goal
    form_class = GoalForm
    template_name = "core/goal_form.html"
    allowed_roles = ()
    success_url = reverse_lazy("goals:goal_list")

    def form_valid(self, form):
        messages.success(self.request, "Maqsad yangilandi.")
        return super().form_valid(form)


class GoalMainView(RoleRequiredMixin, View):
    """Maqsadni bosh sahifada ko'rsatiladigan asosiy maqsad qilish."""

    allowed_roles = ()

    def post(self, request, pk):
        goal = get_object_or_404(Goal, pk=pk)
        goal.is_main = True
        goal.save()
        messages.success(request, "Asosiy maqsad o'zgartirildi.")
        return redirect("goals:goal_list")


class GoalDeleteView(RoleRequiredMixin, View):
    allowed_roles = ()

    def post(self, request, pk):
        get_object_or_404(Goal, pk=pk).delete()
        messages.success(request, "Maqsad o'chirildi.")
        return redirect("goals:goal_list")


class ReportsView(LoginRequiredMixin, TemplateView):
    """Barcha hisobotlar bir joyda (rolga mos)."""

    template_name = "core/reports.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["sections"] = reports_for(self.request.user)
        return context
