"""Views for authentication, dashboard, and role testing."""

from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView as BaseLoginView
from django.contrib.auth.views import LogoutView as BaseLogoutView
from django.contrib.auth.views import PasswordChangeView
from django.core.exceptions import ObjectDoesNotExist
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from apps.core.daily_stats import get_daily_stats, resolve_period
from apps.core.filters import parse_int
from apps.core.services import (
    compute_room_occupancy,
    get_dashboard_metrics,
    get_schedule_grid_data,
    get_teacher_today_lessons,
)
from apps.core.trends import dashboard_trends

from .forms import LoginForm
from .models import User
from .permissions import RoleRequiredMixin

DAILY_STATS_SESSION_KEY = "daily_stats_pending"


class LoginView(BaseLoginView):
    """Страница входа в систему."""

    form_class = LoginForm
    template_name = "accounts/login.html"
    redirect_authenticated_user = True

    def form_valid(self, form):
        response = super().form_valid(form)
        # Kirgandan keyin bosh sahifada kunlik statistika oynasi bir marta ochiladi
        self.request.session[DAILY_STATS_SESSION_KEY] = True
        return response


class LogoutView(BaseLogoutView):
    """Выход из системы (только POST-запрос в Django 5+)."""

    next_page = reverse_lazy("accounts:login")


def recent_students(limit: int = 5):
    """Bosh sahifa: oxirgi qo'shilgan o'quvchilar, birinchi ochiq guruhi va qo'shilgan vaqti."""
    from django.db.models import Prefetch

    from apps.groups.models import Enrollment
    from apps.students.models import Student

    open_enrollments = Prefetch(
        "enrollments",
        queryset=Enrollment.objects.filter(
            status__in=[Enrollment.Status.ACTIVE, Enrollment.Status.TRIAL, Enrollment.Status.FROZEN]
        ).select_related("group").order_by("-start_date", "-pk"),
        to_attr="open_list",
    )
    students = list(Student.objects.order_by("-created_at", "-pk").prefetch_related(open_enrollments)[:limit])
    today = timezone.localdate()
    for s in students:
        s.recent_group = s.open_list[0].group.name if s.open_list else ""
        added = timezone.localtime(s.created_at)
        if added.date() == today:
            s.added_label = f"Bugun {added:%H:%M}"
        elif added.date() == today - timedelta(days=1):
            s.added_label = f"Kecha {added:%H:%M}"
        else:
            s.added_label = f"{added:%d.%m.%Y}"
    return students


class HomeView(LoginRequiredMixin, TemplateView):
    """Главная страница после авторизации."""

    template_name = "accounts/home.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.user.role == User.Role.ACCOUNTANT:
            return redirect("finance:daily_cash")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        # Check if user is teacher
        is_teacher = user.role == User.Role.TEACHER

        if is_teacher:
            # Teacher sees only their today's lessons
            context["is_teacher"] = True
            try:
                teacher = user.teacher_profile
            except ObjectDoesNotExist:
                # O'qituvchi kartasi ulanmagan foydalanuvchi: bo'sh bosh sahifa
                teacher = None
            if teacher is None:
                context["today_lessons"] = []
                context["schedule_grid"] = None
            else:
                context["today_lessons"] = get_teacher_today_lessons(teacher)
                # Teacher sees schedule grid filtered by their groups
                context["schedule_grid"] = get_schedule_grid_data(
                    weekday=date.today().weekday(), step_minutes=30, teacher=teacher
                )
        else:
            # Director and Administrator see full dashboard
            context["is_teacher"] = False
            context["metrics"] = get_dashboard_metrics(user)
            context["room_occupancy"] = compute_room_occupancy()
            # Full schedule grid for admin/director
            today_weekday = date.today().weekday()
            context["schedule_grid"] = get_schedule_grid_data(
                weekday=today_weekday, step_minutes=30
            )
            context["recent_students"] = recent_students()
            context["trends"] = dashboard_trends(user)

        # Weekday names for tabs
        context["weekday_names"] = [
            "Dushanba",
            "Seshanba",
            "Chorshanba",
            "Payshanba",
            "Juma",
            "Shanba",
            "Yakshanba",
        ]
        context["today_weekday"] = date.today().weekday()

        # Kunlik statistika oynasi: kirgandan keyin faqat bir marta avtomatik ochiladi
        # (belgi sessiyadan o'chiriladi, sahifani yangilash oynani qayta ochmaydi)
        local_today = timezone.localdate()
        context["auto_open_daily_stats"] = bool(
            self.request.session.pop(DAILY_STATS_SESSION_KEY, False)
        )
        context["stats_today"] = local_today.isoformat()
        context["stats_yesterday"] = (local_today - timedelta(days=1)).isoformat()

        return context


class DailyStatsView(LoginRequiredMixin, View):
    """Kunlik statistika oynasining ichki qismi (HTML bo'lagi). Huquqlar servisda hisobga olinadi."""

    template_name = "accounts/_daily_stats.html"

    def get(self, request):
        period, day = resolve_period(
            request.GET.get("period", ""), request.GET.get("date", "")
        )
        stats = get_daily_stats(request.user, day)
        response = render(
            request, self.template_name, {"stats": stats, "period": period}
        )
        response["Cache-Control"] = "private, no-store"
        return response


class AdminTestView(RoleRequiredMixin, TemplateView):
    """
    Тестовая страница для роли Администратора.
    Доступ имеют: administrator, а также director (по общему правилу).
    """

    allowed_roles = (User.Role.ADMINISTRATOR,)
    template_name = "accounts/admin_test.html"


class TeacherTestView(RoleRequiredMixin, TemplateView):
    """
    Тестовая страница для роли Учителя.
    Доступ имеют: teacher, а также director (по общему правилу).
    """

    allowed_roles = (User.Role.TEACHER,)
    template_name = "accounts/teacher_test.html"


class DashboardScheduleGridView(LoginRequiredMixin, TemplateView):
    """HTMX view for schedule grid partial rendering."""

    template_name = "accounts/_dashboard_schedule_grid.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        # Get parameters from request
        weekday = parse_int(self.request.GET.get("weekday"), 0, 6)
        step_minutes = parse_int(self.request.GET.get("step"))

        # Noto'g'ri qiymatlar xato bermaydi: bugungi kun va 30 daqiqa
        if weekday is None:
            weekday = timezone.localdate().weekday()
        if step_minutes not in [15, 30, 60]:
            step_minutes = 30

        # Check if user is teacher
        teacher = None
        if user.role == User.Role.TEACHER:
            try:
                teacher = user.teacher_profile
            except ObjectDoesNotExist:
                teacher = None

        # Get schedule grid data
        context["schedule_grid"] = get_schedule_grid_data(
            weekday=weekday, step_minutes=step_minutes, teacher=teacher
        )
        context["weekday_names"] = [
            "Dushanba",
            "Seshanba",
            "Chorshanba",
            "Payshanba",
            "Juma",
            "Shanba",
            "Yakshanba",
        ]

        return context



# ---- Xodimlar boshqaruvi (faqat direktor) ----


class StaffListView(RoleRequiredMixin, TemplateView):
    """Foydalanuvchilar ro'yxati. allowed_roles bo'sh: faqat direktor."""

    allowed_roles = ()
    template_name = "accounts/staff_list.html"

    def get_context_data(self, **kwargs):
        from django.db.models import Q

        context = super().get_context_data(**kwargs)
        users = User.objects.order_by("-is_active", "role", "username")
        q = self.request.GET.get("q", "").strip()
        role = self.request.GET.get("role", "")
        if q:
            users = users.filter(
                Q(username__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q)
            )
        if role in User.Role.values:
            users = users.filter(role=role)
        context.update(users=users, q=q, current_role=role, roles=User.Role.choices)
        return context


class StaffCreateView(RoleRequiredMixin, View):
    allowed_roles = ()
    template_name = "accounts/staff_form.html"

    def get(self, request):
        from .forms import StaffCreateForm

        return render(request, self.template_name, {"form": StaffCreateForm(), "creating": True})

    def post(self, request):
        from .forms import StaffCreateForm

        form = StaffCreateForm(request.POST)
        if form.is_valid():
            from django.core.exceptions import ValidationError

            from apps.tenants.billing import check_users_limit

            try:
                check_users_limit(1)  # tarif limiti (yangi foydalanuvchi faol bo'ladi)
            except ValidationError as exc:
                form.add_error(None, exc.message)
                return render(request, self.template_name, {"form": form, "creating": True})
            user = form.save()
            messages.success(
                request,
                f"Foydalanuvchi yaratildi: login «{user.username}», roli: {user.get_role_display()}.",
            )
            return redirect("accounts:staff_list")
        return render(request, self.template_name, {"form": form, "creating": True})


class StaffUpdateView(RoleRequiredMixin, View):
    allowed_roles = ()
    template_name = "accounts/staff_form.html"

    def _user(self, pk):
        from django.shortcuts import get_object_or_404

        return get_object_or_404(User, pk=pk)

    def get(self, request, pk):
        from .forms import StaffUpdateForm

        target = self._user(pk)
        return render(
            request,
            self.template_name,
            {"form": StaffUpdateForm(instance=target, editor=request.user), "target": target},
        )

    def post(self, request, pk):
        from .forms import StaffUpdateForm

        target = self._user(pk)
        was_active = target.is_active
        form = StaffUpdateForm(request.POST, instance=target, editor=request.user)
        if form.is_valid():
            if not was_active and form.cleaned_data.get("is_active"):
                from django.core.exceptions import ValidationError

                from apps.tenants.billing import check_users_limit

                try:
                    check_users_limit(1)  # qayta yoqilgan foydalanuvchi limitga kiradi
                except ValidationError as exc:
                    form.add_error(None, exc.message)
                    return render(request, self.template_name, {"form": form, "target": target})
            form.save()
            messages.success(request, "Ma'lumotlar saqlandi.")
            return redirect("accounts:staff_list")
        return render(request, self.template_name, {"form": form, "target": target})


class StaffPasswordView(RoleRequiredMixin, View):
    allowed_roles = ()
    template_name = "accounts/staff_password.html"

    def _user(self, pk):
        from django.shortcuts import get_object_or_404

        return get_object_or_404(User, pk=pk)

    def get(self, request, pk):
        from .forms import StaffPasswordForm

        target = self._user(pk)
        return render(request, self.template_name, {"form": StaffPasswordForm(user=target), "target": target})

    def post(self, request, pk):
        from .forms import StaffPasswordForm

        target = self._user(pk)
        form = StaffPasswordForm(request.POST, user=target)
        if form.is_valid():
            form.save()
            from apps.core.audit import log_password_change

            log_password_change(target, request.user)
            messages.success(request, f"«{target.username}» uchun yangi parol o'rnatildi.")
            return redirect("accounts:staff_list")
        return render(request, self.template_name, {"form": form, "target": target})


class SelfPasswordChangeView(LoginRequiredMixin, PasswordChangeView):
    """Har bir foydalanuvchi o'z parolini o'zgartiradi (eski parolni kiritib). Sessiya saqlanadi."""

    template_name = "accounts/password_change.html"
    success_url = reverse_lazy("accounts:home")

    def form_valid(self, form):
        response = super().form_valid(form)
        from apps.core.audit import log_password_change

        log_password_change(self.request.user, self.request.user, own=True)
        messages.success(self.request, "Parol o'zgartirildi.")
        return response

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        for field in form.fields.values():
            field.widget.attrs.setdefault("class", "w-full px-3 py-2 border border-slate-300 rounded-lg")
        return form
