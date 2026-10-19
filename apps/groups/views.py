"""Views for Group and Enrollment management."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from apps.accounts.models import User
from apps.accounts.permissions import RoleRequiredMixin
from apps.attendance.services import mark_attendance
from apps.core.filters import search_tokens
from apps.core.http import safe_next_url
from apps.core.pagination import QuerystringMixin
from apps.finance.models import LedgerEntry
from apps.groups import grid, services
from apps.groups.forms import (
    EnrollmentPriceForm,
    EnrollStudentForm,
    GroupForm,
    TransferStudentForm,
)
from apps.groups.models import Enrollment, Group
from apps.schedule.models import Lesson
from apps.students.models import Student
from apps.teachers.models import Teacher


class GroupListView(QuerystringMixin, RoleRequiredMixin, ListView):
    """Guruhlar ro'yxati."""

    model = Group
    template_name = "groups/group_list.html"
    context_object_name = "groups"
    paginate_by = 20
    allowed_roles = [User.Role.ADMINISTRATOR, User.Role.TEACHER]

    def get_queryset(self):
        user = self.request.user
        if user.role == User.Role.TEACHER:
            qs = Group.objects.filter(teacher__user=user)
        else:
            qs = Group.objects.all()

        qs = qs.select_related("course", "teacher").prefetch_related("enrollments")

        # Holati bo'yicha filtr
        status = self.request.GET.get("status", "").strip()
        if status in [Group.Status.ACTIVE, Group.Status.CLOSED]:
            qs = qs.filter(status=status)

        # Qidiruv
        # Qidiruv: guruh yoki kurs nomi, har bir so'z mos kelishi kerak
        for token in search_tokens(self.request.GET.get("q", "")):
            qs = qs.filter(Q(name__icontains=token) | Q(course__name__icontains=token))

        return qs.order_by("-id")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["current_q"] = self.request.GET.get("q", "").strip()
        context["current_status"] = self.request.GET.get("status", "").strip()
        return context


class GroupDetailView(RoleRequiredMixin, DetailView):
    """Guruh kartochkasi: ma'lumotlar va o'quvchilar ro'yxati."""

    model = Group
    template_name = "groups/group_detail.html"
    context_object_name = "group"
    allowed_roles = [User.Role.ADMINISTRATOR, User.Role.TEACHER]

    def get_queryset(self):
        return Group.objects.select_related("course", "teacher").prefetch_related(
            "enrollments__student"
        )

    def get_object(self, queryset=None):
        group = super().get_object(queryset)
        user = self.request.user
        if user.role == User.Role.TEACHER:
            if not (group.teacher and group.teacher.user == user):
                raise PermissionDenied(
                    "O'qituvchi faqat o'ziga biriktirilgan guruhlarni ko'ra oladi."
                )
        return group

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        group = self.object
        context["active_enrollments"] = group.enrollments.filter(
            status=Enrollment.Status.ACTIVE
        ).select_related("student")
        context["trial_enrollments"] = group.enrollments.filter(
            status=Enrollment.Status.TRIAL
        ).select_related("student")
        context["frozen_enrollments"] = group.enrollments.filter(
            status=Enrollment.Status.FROZEN
        ).select_related("student")
        context["past_enrollments"] = group.enrollments.filter(
            status=Enrollment.Status.FINISHED
        ).select_related("student")
        context["enroll_form"] = EnrollStudentForm()

        today = timezone.localdate()
        month_start = today.replace(day=1)
        raw = self.request.GET.get("month", "")
        try:
            year, month = (int(part) for part in raw.split("-"))
            month_start = month_start.replace(year=year, month=month)
        except ValueError:
            pass
        context["month_label"] = grid.month_label(month_start)
        context["month_tabs"] = grid.month_tabs(month_start, today)
        context["grid"] = grid.attendance_grid(group, month_start, today)
        context["schedule_rows"] = grid.schedule_rows(group)
        balances = grid.student_balances(group)
        order = {Enrollment.Status.ACTIVE: 0, Enrollment.Status.TRIAL: 1, Enrollment.Status.FROZEN: 2}
        members = [
            e
            for e in group.enrollments.select_related("student")
            if e.status in order
        ]
        members.sort(key=lambda e: (order[e.status], e.student.last_name, e.student.first_name))
        for e in members:
            e.balance = balances.get(e.student_id, 0)
        context["members"] = members
        context["today"] = today
        context["can_manage"] = self.request.user.role != User.Role.TEACHER
        context["percent_info"] = group.resolve_teacher_percent() if self.request.user.role == User.Role.DIRECTOR else None
        return context


class GroupAttendanceView(RoleRequiredMixin, View):
    """Guruh jadvalidagi katakni bir klikda belgilash (JSON javob)."""

    allowed_roles = [User.Role.ADMINISTRATOR, User.Role.TEACHER]

    def post(self, request, pk):
        group = get_object_or_404(Group.objects.select_related("teacher"), pk=pk)
        if request.user.role == User.Role.TEACHER and not (
            group.teacher and group.teacher.user == request.user
        ):
            raise PermissionDenied("O'qituvchi faqat o'z guruhida davomat belgilay oladi.")

        lesson = get_object_or_404(Lesson, pk=request.POST.get("lesson_id", 0), group=group)
        student = get_object_or_404(Student, pk=request.POST.get("student_id", 0))
        if lesson.date > timezone.localdate():
            return JsonResponse({"ok": False, "error": "Dars hali o'tmagan."}, status=400)
        try:
            attendance = mark_attendance(
                lesson=lesson, student=student, status=request.POST.get("status", ""), user=request.user
            )
        except ValidationError as e:
            return JsonResponse({"ok": False, "error": " ".join(e.messages)}, status=400)
        return JsonResponse(
            {
                "ok": True,
                "state": attendance.status,
                "label": grid.STATE_LABEL[attendance.status],
                "lesson_held": getattr(attendance, "lesson_marked_held", False),
            }
        )


def _teacher_base_percents():
    """Guruh formasidagi izoh uchun: o'qituvchi -> profilidagi asosiy foiz."""
    return {str(pk): base for pk, base in Teacher.objects.values_list("pk", "base_percent")}


class GroupCreateView(RoleRequiredMixin, CreateView):
    """Yangi guruh yaratish."""

    model = Group
    form_class = GroupForm
    template_name = "groups/group_form.html"
    allowed_roles = [User.Role.ADMINISTRATOR]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["teacher_base_percents"] = _teacher_base_percents()
        return context

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        messages.success(self.request, "Guruh muvaffaqiyatli yaratildi.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy("groups:group_detail", kwargs={"pk": self.object.pk})


class GroupUpdateView(RoleRequiredMixin, UpdateView):
    """Guruh ma'lumotlarini tahrirlash."""

    model = Group
    form_class = GroupForm
    template_name = "groups/group_form.html"
    allowed_roles = [User.Role.ADMINISTRATOR]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["teacher_base_percents"] = _teacher_base_percents()
        return context

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        if "teacher" in form.changed_data:
            from apps.schedule.services import reassign_future_lessons

            moved, conflicts = reassign_future_lessons(self.object, self.object.teacher)
            if moved:
                messages.info(self.request, f"Kelajakdagi {moved} ta dars yangi o'qituvchiga o'tkazildi.")
            if conflicts:
                messages.warning(self.request, f"{conflicts} ta darsda yangi o'qituvchi boshqa guruhda band (vaqt to'qnashuvi). Jadvalni tekshiring.")
        if "status" in form.changed_data and self.object.status == Group.Status.CLOSED:
            result = services.close_group(self.object, user=self.request.user)
            messages.info(
                self.request,
                f"Guruh yopildi: {result['enrollments']} ta a'zolik tugatildi, "
                f"{result['lessons']} ta kelajakdagi dars bekor qilindi.",
            )
        messages.success(self.request, "Guruh ma'lumotlari yangilandi.")
        return response

    def get_success_url(self):
        return reverse_lazy("groups:group_detail", kwargs={"pk": self.object.pk})


class GroupEnrollView(RoleRequiredMixin, View):
    """O'quvchini guruhga yozish (services.py orqali)."""

    allowed_roles = [User.Role.ADMINISTRATOR]

    def post(self, request, pk):
        group = get_object_or_404(Group, pk=pk)
        form = EnrollStudentForm(request.POST)
        if form.is_valid():
            student = form.cleaned_data["student"]
            start_date = form.cleaned_data.get("start_date")
            status = form.cleaned_data.get("status", Enrollment.Status.ACTIVE)
            try:
                enrollment = services.enroll_student(
                    student=student,
                    group=group,
                    start_date=start_date,
                    status=status,
                    user=request.user,
                    custom_price=form.cleaned_data.get("custom_price"),
                )
                messages.success(
                    request,
                    f"{student.full_name} guruhga muvaffaqiyatli qo'shildi.",
                )
                if enrollment.backfilled_charges:
                    charged = -(
                        LedgerEntry.objects.filter(enrollment=enrollment, kind=LedgerEntry.Kind.CHARGE).aggregate(
                            total=Sum("amount")
                        )["total"]
                        or 0
                    )
                    messages.info(
                        request,
                        f"Boshlanish sanasidan beri {enrollment.backfilled_charges} ta dars uchun "
                        f"{charged:,} so'm hisoblandi.".replace(",", " "),
                    )
                elif (
                    enrollment.status == Enrollment.Status.ACTIVE
                    and enrollment.start_date < timezone.localdate()
                    and not Lesson.objects.filter(
                        group=group, date__gte=enrollment.start_date, date__lt=timezone.localdate()
                    ).exists()
                ):
                    messages.warning(
                        request,
                        "Bu davr uchun jadvalda dars yo'q, shuning uchun to'lov hisoblanmadi. "
                        "Avval guruhga darslarni yarating.",
                    )
            except ValidationError as e:
                messages.error(request, e.message)
        else:
            messages.error(request, "Forma ma'lumotlari noto'g'ri kiritildi.")

        # O'quvchi kartasidan qo'shilganda o'sha sahifaga qaytiladi (faqat shu sayt ichida)
        next_url = request.POST.get("next", "")
        if next_url and url_has_allowed_host_and_scheme(
            next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        ):
            return redirect(next_url)
        return redirect("groups:group_detail", pk=pk)


class GroupTrialConvertView(RoleRequiredMixin, View):
    """Sinov darsidagi o'quvchini faol holatga o'tkazish (services.py orqali)."""

    allowed_roles = [User.Role.ADMINISTRATOR]

    def post(self, request, pk, enrollment_id):
        group = get_object_or_404(Group, pk=pk)
        enrollment = get_object_or_404(Enrollment, pk=enrollment_id, group=group)
        paid_from_raw = request.POST.get("paid_from")
        paid_from = None
        if paid_from_raw:
            try:
                from datetime import datetime

                paid_from = datetime.strptime(paid_from_raw, "%Y-%m-%d").date()
            except ValueError:
                pass

        try:
            services.convert_trial_to_active(
                enrollment=enrollment, paid_from=paid_from, user=request.user
            )
            messages.success(
                request,
                f"{enrollment.student.full_name} sinov darsidan faol a'zolikka muvaffaqiyatli o'tkazildi.",
            )
        except ValidationError as e:
            messages.error(request, e.message)

        next_url = safe_next_url(request)
        if next_url:
            return redirect(next_url)
        return redirect("groups:group_detail", pk=pk)


class GroupFreezeView(RoleRequiredMixin, View):
    """Guruhdagi o'quvchi a'zoligini muzlatish."""

    allowed_roles = [User.Role.ADMINISTRATOR, User.Role.TEACHER]

    def post(self, request, pk, enrollment_id):
        group = get_object_or_404(Group, pk=pk)
        enrollment = get_object_or_404(Enrollment, pk=enrollment_id, group=group)
        try:
            services.freeze_enrollment(enrollment=enrollment, user=request.user)
            messages.success(
                request,
                f"{enrollment.student.full_name} a'zoligi muzlatildi.",
            )
        except ValidationError as e:
            messages.error(request, e.message)

        next_url = safe_next_url(request)
        if next_url:
            return redirect(next_url)
        return redirect("groups:group_detail", pk=pk)


class GroupUnfreezeView(RoleRequiredMixin, View):
    """Guruhdagi muzlatilgan o'quvchi a'zoligini faollashtirish (muzlatishni bekor qilish)."""

    allowed_roles = [User.Role.ADMINISTRATOR, User.Role.TEACHER]

    def post(self, request, pk, enrollment_id):
        group = get_object_or_404(Group, pk=pk)
        enrollment = get_object_or_404(Enrollment, pk=enrollment_id, group=group)
        date_raw = request.POST.get("date")
        unfreeze_date = None
        if date_raw:
            try:
                from datetime import datetime

                unfreeze_date = datetime.strptime(date_raw, "%Y-%m-%d").date()
            except ValueError:
                pass

        try:
            services.unfreeze_enrollment(
                enrollment=enrollment, user=request.user, date=unfreeze_date
            )
            messages.success(
                request,
                f"{enrollment.student.full_name} a'zoligi muzlatishdan chiqarildi.",
            )
        except ValidationError as e:
            messages.error(request, e.message)

        next_url = safe_next_url(request)
        if next_url:
            return redirect(next_url)
        return redirect("groups:group_detail", pk=pk)


class GroupRemoveView(RoleRequiredMixin, View):
    """O'quvchini guruhdan chiqarish (services.py orqali)."""

    allowed_roles = [User.Role.ADMINISTRATOR]

    def post(self, request, pk, student_id):
        group = get_object_or_404(Group, pk=pk)
        student = get_object_or_404(Student, pk=student_id)
        try:
            services.remove_student(student=student, group=group, user=request.user)
            messages.success(
                request,
                f"{student.full_name} guruhdan muvaffaqiyatli chiqarildi.",
            )
        except ValidationError as e:
            messages.error(request, e.message)

        next_url = safe_next_url(request)
        if next_url:
            return redirect(next_url)
        return redirect("groups:group_detail", pk=pk)


class GroupTransferView(RoleRequiredMixin, View):
    """O'quvchini boshqa guruhga ko'chirish (administrator va direktor)."""

    allowed_roles = [User.Role.ADMINISTRATOR]
    template_name = "groups/transfer.html"

    def _form(self, group, data=None):
        form = TransferStudentForm(data) if data is not None else TransferStudentForm(initial={"start_date": timezone.localdate()})
        form.fields["to_group"].queryset = form.fields["to_group"].queryset.exclude(pk=group.pk)
        return form

    def _context(self, request, group, student, form):
        from apps.finance.services import get_balance

        return {
            "group": group,
            "student": student,
            "form": form,
            "balance": get_balance(student, group),
            "can_carry": request.user.role == User.Role.DIRECTOR,
        }

    def get(self, request, pk, student_id):
        group = get_object_or_404(Group, pk=pk)
        student = get_object_or_404(Student, pk=student_id)
        return render(request, self.template_name, self._context(request, group, student, self._form(group)))

    def post(self, request, pk, student_id):
        group = get_object_or_404(Group, pk=pk)
        student = get_object_or_404(Student, pk=student_id)
        form = self._form(group, request.POST)
        if form.is_valid():
            data = form.cleaned_data
            try:
                services.transfer_student(
                    student,
                    group,
                    data["to_group"],
                    user=request.user,
                    start_date=data["start_date"],
                    carry_balance=data["carry_balance"],
                    note=data["note"],
                )
                messages.success(request, f"{student.full_name} {data['to_group'].name} guruhiga ko'chirildi.")
                return redirect("groups:group_detail", pk=data["to_group"].pk)
            except PermissionDenied as exc:
                form.add_error(None, str(exc))
            except ValidationError as exc:
                form.add_error(None, "; ".join(exc.messages))
        return render(request, self.template_name, self._context(request, group, student, form))



class EnrollmentPriceView(RoleRequiredMixin, View):
    """O'quvchining guruhdagi shaxsiy narxini ko'rish va o'zgartirish (administrator va direktor)."""

    allowed_roles = [User.Role.ADMINISTRATOR]
    template_name = "groups/enrollment_price.html"

    def _enrollment(self, pk):
        return get_object_or_404(
            Enrollment.objects.select_related("student", "group__course"),
            pk=pk,
            status__in=[Enrollment.Status.ACTIVE, Enrollment.Status.TRIAL, Enrollment.Status.FROZEN],
        )

    def get(self, request, pk):
        enrollment = self._enrollment(pk)
        form = EnrollmentPriceForm(initial={"price": enrollment.custom_price, "note": enrollment.price_note})
        return render(request, self.template_name, {"enrollment": enrollment, "form": form})

    def post(self, request, pk):
        from apps.finance import services as finance_services

        enrollment = self._enrollment(pk)
        form = EnrollmentPriceForm(request.POST)
        if form.is_valid():
            try:
                fixes = finance_services.set_enrollment_price(
                    enrollment, form.cleaned_data["price"], request.user, form.cleaned_data["note"]
                )
            except (ValidationError, PermissionDenied) as exc:
                messages.error(request, getattr(exc, "message", str(exc)))
            else:
                price = form.cleaned_data["price"]
                text = "Kurs narxiga qaytarildi." if price is None else f"Shaxsiy narx o'rnatildi: {price:,} so'm.".replace(",", " ")
                if fixes:
                    text += f" Hisoblangan {fixes} oy yangi narxga to'g'rilandi."
                messages.success(request, text)
                return redirect(request.POST.get("next") or reverse("groups:group_detail", args=[enrollment.group_id]))
        return render(request, self.template_name, {"enrollment": enrollment, "form": form})
