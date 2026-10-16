"""Schedule views: weekly grid, lesson detail, lesson generation."""

from datetime import date, timedelta
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from apps.accounts.models import User
from apps.accounts.permissions import RoleRequiredMixin
from apps.attendance.models import Attendance
from apps.attendance.services import mark_attendance
from apps.core.filters import parse_int
from apps.groups.models import Enrollment, Group
from apps.schedule import services
from apps.schedule.forms import (
    CancelLessonForm,
    GenerateLessonsForm,
    RescheduleLessonForm,
    RoomForm,
    ScheduleSlotForm,
)
from apps.schedule.models import Lesson, Room, ScheduleSlot
from apps.teachers.models import Teacher

WEEKDAY_NAMES = ["Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba", "Yakshanba"]


class WeeklyScheduleView(RoleRequiredMixin, View):
    """Dars jadvali: oy (standart), hafta yoki xonalar bandligi ko'rinishi."""

    allowed_roles = [User.Role.ADMINISTRATOR, User.Role.TEACHER]
    template_name = "schedule/weekly_schedule.html"
    MAX_IN_MONTH_CELL = 5

    def _base_queryset(self, request):
        qs = Lesson.objects.select_related("group", "room", "teacher")
        # RBAC: teacher faqat o'z darslarini ko'radi
        if request.user.role == User.Role.TEACHER:
            teacher_profile = Teacher.objects.filter(user=request.user).first()
            qs = qs.filter(teacher=teacher_profile) if teacher_profile else qs.none()
        return qs

    def get(self, request):
        today = date.today()
        mode = request.GET.get("view")
        if mode not in ("month", "week", "rooms"):
            # Eski havolalar (?week=...) haftalik ko'rinishni ochadi
            mode = "week" if request.GET.get("week") else "month"

        group_filter = parse_int(request.GET.get("group"))
        teacher_filter = parse_int(request.GET.get("teacher"))
        room_filter = parse_int(request.GET.get("room"))
        filter_params = {
            key: value
            for key, value in (("group", group_filter), ("teacher", teacher_filter), ("room", room_filter))
            if value is not None
        }

        qs = self._base_queryset(request)
        if group_filter is not None:
            qs = qs.filter(group_id=group_filter)
        if teacher_filter is not None:
            qs = qs.filter(teacher_id=teacher_filter)
        if room_filter is not None:
            qs = qs.filter(room_id=room_filter)

        context = {
            "view_mode": mode,
            "today": today,
            "today_weekday": today.weekday(),
            "weekday_names": WEEKDAY_NAMES,
            "filter_qs": urlencode(filter_params),
            "groups": Group.objects.filter(status="active").order_by("name"),
            "teachers": Teacher.objects.filter(is_active=True).order_by("full_name"),
            "rooms": Room.objects.filter(is_active=True).order_by("name"),
            "current_group": "" if group_filter is None else str(group_filter),
            "current_teacher": "" if teacher_filter is None else str(teacher_filter),
            "current_room": "" if room_filter is None else str(room_filter),
        }

        if mode == "month":
            month_start = _parse_month(request.GET.get("month"), today)
            next_month = (month_start + timedelta(days=32)).replace(day=1)
            grid_start = month_start - timedelta(days=month_start.weekday())
            grid_end = next_month - timedelta(days=1)
            grid_end += timedelta(days=6 - grid_end.weekday())
            days = [grid_start + timedelta(days=i) for i in range((grid_end - grid_start).days + 1)]
            lessons_by_day = {d: [] for d in days}
            for lesson in qs.filter(date__range=(grid_start, grid_end)).order_by("date", "start_time"):
                lessons_by_day[lesson.date].append(lesson)
            context.update(
                month_start=month_start,
                month_title=f"{MONTH_NAMES[month_start.month - 1]} {month_start.year}",
                prev_month=(month_start - timedelta(days=1)).strftime("%Y-%m"),
                next_month=next_month.strftime("%Y-%m"),
                this_month=today.strftime("%Y-%m"),
                lessons_by_day=lessons_by_day,
                month_weeks=[days[i:i + 7] for i in range(0, len(days), 7)],
                max_in_cell=self.MAX_IN_MONTH_CELL,
                more_offset=-self.MAX_IN_MONTH_CELL,
                month_total=sum(len(v) for d, v in lessons_by_day.items() if d.month == month_start.month),
            )
        elif mode == "week":
            week_start = _parse_iso_date(request.GET.get("week"), today)
            week_start -= timedelta(days=week_start.weekday())
            week_end = week_start + timedelta(days=6)
            week_days = [week_start + timedelta(days=i) for i in range(7)]
            lessons_by_day = {d: [] for d in week_days}
            for lesson in qs.filter(date__range=(week_start, week_end)).order_by("date", "start_time"):
                lessons_by_day[lesson.date].append(lesson)
            context.update(
                week_days=week_days,
                lessons_by_day=lessons_by_day,
                week_start=week_start,
                week_end=week_end,
                prev_week=(week_start - timedelta(days=7)).isoformat(),
                next_week=(week_start + timedelta(days=7)).isoformat(),
            )
        return render(request, self.template_name, context)


MONTH_NAMES = [
    "Yanvar", "Fevral", "Mart", "Aprel", "May", "Iyun",
    "Iyul", "Avgust", "Sentabr", "Oktabr", "Noyabr", "Dekabr",
]


def _parse_iso_date(value, default):
    try:
        return date.fromisoformat(value) if value else default
    except ValueError:
        return default


def _parse_month(value, default):
    """'2026-10' -> date(2026, 10, 1). Noto'g'ri qiymatda joriy oy."""
    try:
        year, month = (int(part) for part in (value or "").split("-"))
        return date(year, month, 1)
    except (ValueError, TypeError):
        return default.replace(day=1)


class LessonDetailView(RoleRequiredMixin, View):
    """Dars sahifasi: davomat, holat o'zgartirish, bekor qilish, ko'chirish."""

    allowed_roles = [User.Role.ADMINISTRATOR, User.Role.TEACHER]
    template_name = "schedule/lesson_detail.html"

    def _get_lesson(self, pk, user):
        lesson = get_object_or_404(
            Lesson.objects.select_related("group", "room", "teacher", "group__course"),
            pk=pk,
        )
        # Teacher o'z darsini ko'ra oladi
        if user.role == User.Role.TEACHER:
            try:
                teacher_profile = Teacher.objects.get(user=user)
            except Teacher.DoesNotExist:
                raise PermissionDenied from None
            if lesson.teacher != teacher_profile:
                raise PermissionDenied("O'qituvchi faqat o'z darsini ko'ra oladi.")
        return lesson

    def _get_enrolled_students(self, lesson):
        """Shu guruhda dars sanasida faol a'zolikdagi o'quvchilar."""
        from django.db.models import Q

        from apps.students.models import Student

        return (
            Student.objects.filter(
                enrollments__group=lesson.group,
                enrollments__status__in=[
                    Enrollment.Status.ACTIVE,
                    Enrollment.Status.TRIAL,
                ],
                enrollments__start_date__lte=lesson.date,
            )
            .filter(
                Q(enrollments__end_date__isnull=True)
                | Q(enrollments__end_date__gte=lesson.date)
            )
            .distinct()
            .order_by("last_name", "first_name")
        )

    def get(self, request, pk):
        lesson = self._get_lesson(pk, request.user)
        students = self._get_enrolled_students(lesson)

        # Davomat yozuvlarini fetch qilish
        att_map = {
            a.student_id: a
            for a in Attendance.objects.filter(lesson=lesson, student__in=students)
        }

        student_rows = []
        for st in students:
            att = att_map.get(st.pk)
            student_rows.append({"student": st, "attendance": att})

        context = {
            "lesson": lesson,
            "student_rows": student_rows,
            "attendance_statuses": Attendance.Status.choices,
            "cancel_form": CancelLessonForm(),
            "reschedule_form": RescheduleLessonForm(),
            "can_manage": request.user.role in [User.Role.DIRECTOR, User.Role.ADMINISTRATOR],
        }
        return render(request, self.template_name, context)


class MarkAttendanceView(RoleRequiredMixin, View):
    """HTMX: o'quvchining davomat holatini bir klikda belgilash."""

    allowed_roles = [User.Role.ADMINISTRATOR, User.Role.TEACHER]

    def post(self, request, pk):
        lesson = get_object_or_404(Lesson, pk=pk)
        student_id = request.POST.get("student_id")
        status = request.POST.get("status")

        # Teacher check
        if request.user.role == User.Role.TEACHER:
            try:
                teacher_profile = Teacher.objects.get(user=request.user)
            except Teacher.DoesNotExist:
                raise PermissionDenied from None
            if lesson.teacher != teacher_profile:
                raise PermissionDenied

        from apps.students.models import Student

        student = get_object_or_404(Student, pk=student_id)

        try:
            attendance = mark_attendance(
                lesson=lesson,
                student=student,
                status=status,
                user=request.user,
            )
        except ValidationError as e:
            messages.error(request, e.message)
            return redirect("schedule:lesson_detail", pk=pk)

        held_now = getattr(attendance, "lesson_marked_held", False)
        if held_now:
            messages.success(request, "Davomat belgilandi: dars o'tildi deb belgilandi va to'lovlar hisoblandi.")

        # HTMX javob: faqat shu o'quvchi qatorini qaytarish
        if request.headers.get("HX-Request") == "true":
            response = render(
                request,
                "schedule/_attendance_row.html",
                {
                    "student": student,
                    "attendance": attendance,
                    "lesson": lesson,
                    "attendance_statuses": Attendance.Status.choices,
                    "can_manage": request.user.role in [User.Role.DIRECTOR, User.Role.ADMINISTRATOR],
                },
            )
            if held_now:
                response["HX-Refresh"] = "true"  # dars holati sahifa sarlavhasida ham yangilansin
            return response
        return redirect("schedule:lesson_detail", pk=pk)


class MarkLessonHeldView(RoleRequiredMixin, View):
    """Darsni o'tildi deb belgilash (mark_lesson_held service)."""

    allowed_roles = [User.Role.ADMINISTRATOR, User.Role.TEACHER]

    def post(self, request, pk):
        lesson = get_object_or_404(Lesson, pk=pk)

        # Teacher check
        if request.user.role == User.Role.TEACHER:
            try:
                teacher_profile = Teacher.objects.get(user=request.user)
            except Teacher.DoesNotExist:
                raise PermissionDenied from None
            if lesson.teacher != teacher_profile:
                raise PermissionDenied

        try:
            from apps.schedule.services import mark_lesson_held

            mark_lesson_held(lesson, request.user)
            messages.success(request, "Dars o'tildi deb belgilandi va hisob qilindi.")
        except ValidationError as e:
            messages.error(request, str(e))

        return redirect("schedule:lesson_detail", pk=pk)


class CancelLessonView(RoleRequiredMixin, View):
    """Darsni bekor qilish."""

    allowed_roles = [User.Role.ADMINISTRATOR]

    def post(self, request, pk):
        lesson = get_object_or_404(Lesson, pk=pk)
        form = CancelLessonForm(request.POST)
        if form.is_valid():
            try:
                services.cancel_lesson(lesson, reason=form.cleaned_data.get("reason", ""), user=request.user)
                messages.success(request, "Dars muvaffaqiyatli bekor qilindi.")
            except ValidationError as e:
                messages.error(request, e.message)
        return redirect("schedule:lesson_detail", pk=pk)


class RescheduleLessonView(RoleRequiredMixin, View):
    """Darsni boshqa vaqtga ko'chirish."""

    allowed_roles = [User.Role.ADMINISTRATOR]

    def post(self, request, pk):
        lesson = get_object_or_404(Lesson, pk=pk)
        form = RescheduleLessonForm(request.POST)
        if form.is_valid():
            try:
                services.reschedule_lesson(
                    lesson=lesson,
                    new_date=form.cleaned_data["new_date"],
                    new_start_time=form.cleaned_data["new_start_time"],
                    new_end_time=form.cleaned_data["new_end_time"],
                    new_room=form.cleaned_data.get("new_room"),
                    reason=form.cleaned_data.get("reason", ""),
                )
                messages.success(request, "Dars muvaffaqiyatli ko'chirildi.")
            except ValidationError as e:
                messages.error(request, e.message)
        else:
            messages.error(request, "Forma ma'lumotlari noto'g'ri.")
        return redirect("schedule:lesson_detail", pk=pk)


class GenerateLessonsView(RoleRequiredMixin, View):
    """Guruh uchun davrga darslar generatsiyasi."""

    allowed_roles = [User.Role.ADMINISTRATOR]
    template_name = "schedule/generate_lessons.html"

    def get(self, request):
        form = GenerateLessonsForm()
        return render(request, self.template_name, {"form": form})

    def post(self, request):
        form = GenerateLessonsForm(request.POST)
        if form.is_valid():
            try:
                created = services.generate_lessons(
                    group=form.cleaned_data["group"],
                    date_from=form.cleaned_data["date_from"],
                    date_to=form.cleaned_data["date_to"],
                )
                messages.success(
                    request,
                    f"{len(created)} ta dars muvaffaqiyatli yaratildi.",
                )
                return redirect("schedule:weekly_schedule")
            except ValidationError as e:
                messages.error(request, e.message)
        return render(request, self.template_name, {"form": form})


# ---- Rooms & Slots (directory) ----

class RoomListView(RoleRequiredMixin, ListView):
    model = Room
    template_name = "schedule/room_list.html"
    context_object_name = "rooms"
    allowed_roles = [User.Role.ADMINISTRATOR]

    def get_queryset(self):
        return Room.objects.order_by("-is_active", "name")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Har bir xona: faol guruhlarning haftalik slotlari bo'yicha bandlik (soat) va guruhlar
        usage = {}
        slots = ScheduleSlot.objects.filter(
            is_active=True, group__status=Group.Status.ACTIVE
        ).select_related("group")
        for slot in slots:
            minutes = (slot.end_time.hour * 60 + slot.end_time.minute) - (
                slot.start_time.hour * 60 + slot.start_time.minute
            )
            item = usage.setdefault(slot.room_id, {"minutes": 0, "groups": set()})
            item["minutes"] += max(minutes, 0)
            item["groups"].add(slot.group.name)
        for room in context["rooms"]:
            item = usage.get(room.pk, {"minutes": 0, "groups": set()})
            room.weekly_hours = round(item["minutes"] / 60, 1)
            room.group_names = sorted(item["groups"])
        context["form"] = RoomForm()
        return context


class RoomCreateView(RoleRequiredMixin, CreateView):
    model = Room
    form_class = RoomForm
    template_name = "schedule/room_form.html"
    allowed_roles = [User.Role.ADMINISTRATOR]
    success_url = reverse_lazy("schedule:room_list")

    def form_valid(self, form):
        messages.success(self.request, "Xona muvaffaqiyatli qo'shildi.")
        return super().form_valid(form)


class RoomUpdateView(RoleRequiredMixin, UpdateView):
    model = Room
    form_class = RoomForm
    template_name = "schedule/room_form.html"
    allowed_roles = [User.Role.ADMINISTRATOR]
    success_url = reverse_lazy("schedule:room_list")

    def form_valid(self, form):
        messages.success(self.request, "Xona ma'lumotlari yangilandi.")
        return super().form_valid(form)


class SlotListView(RoleRequiredMixin, ListView):
    model = ScheduleSlot
    template_name = "schedule/slot_list.html"
    context_object_name = "slots"
    allowed_roles = [User.Role.ADMINISTRATOR]

    def get_queryset(self):
        qs = ScheduleSlot.objects.select_related("group", "room").order_by(
            "group__name", "weekday", "start_time"
        )
        group_id = parse_int(self.request.GET.get("group"))
        if group_id is not None:
            qs = qs.filter(group_id=group_id)
        return qs

    def get_context_data(self, **kwargs):
        from apps.groups.models import Group
        ctx = super().get_context_data(**kwargs)
        ctx["groups"] = Group.objects.filter(status="active").order_by("name")
        group_id = parse_int(self.request.GET.get("group"))
        ctx["current_group"] = "" if group_id is None else str(group_id)
        return ctx


class SlotCreateView(RoleRequiredMixin, CreateView):
    model = ScheduleSlot
    form_class = ScheduleSlotForm
    template_name = "schedule/slot_form.html"
    allowed_roles = [User.Role.ADMINISTRATOR]
    success_url = reverse_lazy("schedule:slot_list")

    def form_valid(self, form):
        messages.success(self.request, "Jadval sloti qo'shildi.")
        return super().form_valid(form)


class SlotUpdateView(RoleRequiredMixin, UpdateView):
    model = ScheduleSlot
    form_class = ScheduleSlotForm
    template_name = "schedule/slot_form.html"
    allowed_roles = [User.Role.ADMINISTRATOR]
    success_url = reverse_lazy("schedule:slot_list")

    def form_valid(self, form):
        messages.success(self.request, "Jadval sloti yangilandi.")
        return super().form_valid(form)
