"""Views for Teacher management."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from apps.accounts.models import User
from apps.accounts.permissions import RoleRequiredMixin
from apps.core.filters import search_tokens
from apps.core.pagination import QuerystringMixin
from apps.groups.models import Group
from apps.teachers.forms import TeacherForm
from apps.teachers.models import Teacher
from apps.teachers.services import build_teacher_profile


class TeacherListView(QuerystringMixin, RoleRequiredMixin, ListView):
    """O'qituvchilar ro'yxati."""

    model = Teacher
    template_name = "teachers/teacher_list.html"
    context_object_name = "teachers"
    paginate_by = 20
    allowed_roles = [User.Role.ADMINISTRATOR]

    def get_queryset(self):
        active = Q(groups__status=Group.Status.ACTIVE)
        qs = (
            Teacher.objects.select_related("user")
            .annotate(groups_count=Count("groups", filter=active, distinct=True))
            .prefetch_related(
                Prefetch("groups", queryset=Group.objects.filter(status=Group.Status.ACTIVE), to_attr="active_groups")
            )
        )

        for token in search_tokens(self.request.GET.get("q", "")):
            qs = qs.filter(Q(full_name__icontains=token) | Q(phone__icontains=token))

        # is_active bo'yicha filtr
        is_active_filter = self.request.GET.get("is_active", "").strip()
        if is_active_filter == "true":
            qs = qs.filter(is_active=True)
        elif is_active_filter == "false":
            qs = qs.filter(is_active=False)

        return qs.order_by("-id")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["current_is_active"] = self.request.GET.get("is_active", "")
        context["q"] = self.request.GET.get("q", "").strip()
        context["can_see_pay"] = self.request.user.role == User.Role.DIRECTOR  # maosh — faqat direktor
        # "Foiz" ustuni: har bir faol guruh uchun amaldagi foiz (Group.resolve_teacher_percent qoidasi bilan)
        for teacher in context["teachers"]:
            resolved = [group.resolve_teacher_percent(teacher)[0] for group in teacher.active_groups]
            known = [p for p in resolved if p is not None]
            teacher.percent_min = min(known) if known else None
            teacher.percent_max = max(known) if known else None
            teacher.percent_missing = sum(1 for p in resolved if p is None)
        return context


class TeacherCreateView(RoleRequiredMixin, CreateView):
    """Yangi o'qituvchi qo'shish."""

    model = Teacher
    form_class = TeacherForm
    template_name = "teachers/teacher_form.html"
    allowed_roles = [User.Role.ADMINISTRATOR]
    success_url = reverse_lazy("teachers:teacher_list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        messages.success(self.request, "O'qituvchi muvaffaqiyatli qo'shildi.")
        return super().form_valid(form)


class TeacherUpdateView(RoleRequiredMixin, UpdateView):
    """O'qituvchi ma'lumotlarini tahrirlash."""

    model = Teacher
    form_class = TeacherForm
    template_name = "teachers/teacher_form.html"
    allowed_roles = [User.Role.ADMINISTRATOR]
    success_url = reverse_lazy("teachers:teacher_list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        messages.success(self.request, "O'qituvchi ma'lumotlari yangilandi.")
        return super().form_valid(form)


class TeacherProfileView(RoleRequiredMixin, View):
    """O'qituvchi profili.

    O'qituvchi faqat o'z profilini ko'radi; administrator va direktor istalganini.
    Ish haqi va moliyaviy ma'lumotlar bu sahifada ko'rsatilmaydi.
    """

    allowed_roles = [User.Role.TEACHER, User.Role.ADMINISTRATOR]
    template_name = "teachers/teacher_profile.html"

    def get(self, request, pk=None):
        if pk is None:
            teacher = Teacher.objects.filter(user=request.user).first()
            if teacher is None:
                messages.error(request, "Sizning o'qituvchi profilingiz topilmadi.")
                return redirect("accounts:home")
        else:
            teacher = get_object_or_404(Teacher, pk=pk)
            if request.user.role == User.Role.TEACHER and teacher.user_id != request.user.pk:
                raise PermissionDenied("O'qituvchi faqat o'z profilini ko'ra oladi.")

        show_archive = request.GET.get("archive") == "1"
        context = {
            "teacher": teacher,
            "profile": build_teacher_profile(teacher, show_archive=show_archive),
            "show_archive": show_archive,
            "is_own": teacher.user_id == request.user.pk,
        }
        return render(request, self.template_name, context)
