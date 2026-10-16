"""Views for Course management."""

from django.contrib import messages
from django.urls import reverse_lazy
from django.views.generic import CreateView, ListView, UpdateView

from apps.accounts.models import User
from apps.accounts.permissions import RoleRequiredMixin
from apps.courses.forms import CourseForm
from apps.courses.models import Course


class CourseListView(RoleRequiredMixin, ListView):
    """Kurslar ro'yxati."""

    model = Course
    template_name = "courses/course_list.html"
    context_object_name = "courses"
    allowed_roles = [User.Role.ADMINISTRATOR]

    def get_queryset(self):
        qs = Course.objects.all()

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
        return context


class CourseCreateView(RoleRequiredMixin, CreateView):
    """Yangi kurs qo'shish."""

    model = Course
    form_class = CourseForm
    template_name = "courses/course_form.html"
    allowed_roles = [User.Role.ADMINISTRATOR]
    success_url = reverse_lazy("courses:course_list")

    def form_valid(self, form):
        messages.success(self.request, "Kurs muvaffaqiyatli yaratildi.")
        return super().form_valid(form)


class CourseUpdateView(RoleRequiredMixin, UpdateView):
    """Kurs ma'lumotlarini tahrirlash."""

    model = Course
    form_class = CourseForm
    template_name = "courses/course_form.html"
    allowed_roles = [User.Role.ADMINISTRATOR]
    success_url = reverse_lazy("courses:course_list")

    def form_valid(self, form):
        messages.success(self.request, "Kurs ma'lumotlari yangilandi.")
        return super().form_valid(form)
