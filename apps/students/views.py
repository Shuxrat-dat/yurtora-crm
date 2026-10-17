"""Views for Student and Guardian management."""

from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import BigIntegerField, Count, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import (
    CreateView,
    DetailView,
    ListView,
    TemplateView,
    UpdateView,
)

from apps.accounts.models import User
from apps.accounts.permissions import RoleRequiredMixin, user_has_role
from apps.core.export import safe_row
from apps.core.filters import parse_int, person_search_q
from apps.core.models import AuditLog
from apps.courses.models import Course
from apps.finance.models import LedgerEntry
from apps.groups.models import Enrollment, Group
from apps.schedule.services import group_schedule_summaries
from apps.students import importer
from apps.students.card import attendance_calendar, student_history
from apps.students.forms import GuardianForm, StudentForm
from apps.students.models import Student, StudentGuardian
from apps.teachers.models import Teacher

OPEN_ENROLLMENT_STATUSES = [
    Enrollment.Status.ACTIVE,
    Enrollment.Status.TRIAL,
    Enrollment.Status.FROZEN,
]


class StudentListView(RoleRequiredMixin, ListView):
    """O'quvchilar ro'yxati, filtrlash, qidiruv va sahifalash."""

    model = Student
    template_name = "students/student_list.html"
    context_object_name = "students"
    paginate_by = 15
    allowed_roles = [User.Role.ADMINISTRATOR, User.Role.TEACHER, User.Role.ACCOUNTANT]

    def get_queryset(self):
        user = self.request.user
        if user.role == User.Role.TEACHER:
            # O'qituvchi faqat o'zining faol, sinov yoki muzlatilgan guruhlaridagi o'quvchilarni ko'ra oladi
            qs = (
                Student.objects.filter(
                    enrollments__group__teacher__user=user,
                    enrollments__status__in=[
                        Enrollment.Status.ACTIVE,
                        Enrollment.Status.TRIAL,
                        Enrollment.Status.FROZEN,
                    ],
                )
                .prefetch_related(
                    "student_guardians__guardian",
                    "enrollments__group__teacher",
                )
            )
        else:
            qs = Student.objects.all().prefetch_related(
                "student_guardians__guardian",
                "enrollments__group__teacher",
            )

        # Qidiruv: so'zlar tartibi va registr muhim emas.
        # Telefon bo'yicha qidiruv faqat administrator va direktor uchun.
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(
                person_search_q(q, include_phone=user.role != User.Role.TEACHER)
            )

        # Holati bo'yicha filtr
        status_filter = self.request.GET.get("status", "").strip()
        if status_filter in Student.Status.values:
            qs = qs.filter(status=status_filter)
        elif status_filter == "trial":
            # Bosh sahifadagi "Sinov darsida" kartochkasi: sinov a'zoligi bor o'quvchilar
            qs = qs.filter(enrollments__status=Enrollment.Status.TRIAL)

        # Guruh va o'qituvchi filtrlari bitta a'zolikka tegishli bo'lishi kerak
        # (alohida filter() chaqiruvlari turli a'zoliklarni aralashtirib yuborardi).
        group_id = parse_int(self.request.GET.get("group", ""))
        teacher_id = parse_int(self.request.GET.get("teacher", ""))
        if group_id is not None or teacher_id is not None:
            enrollment_filter = Q(enrollments__status__in=OPEN_ENROLLMENT_STATUSES)
            if group_id is not None:
                enrollment_filter &= Q(enrollments__group_id=group_id)
            if teacher_id is not None:
                enrollment_filter &= Q(enrollments__group__teacher_id=teacher_id)
            if user.role == User.Role.TEACHER:
                # O'qituvchi URL orqali boshqa guruhlarni so'ray olmaydi
                enrollment_filter &= Q(enrollments__group__teacher__user=user)
            qs = qs.filter(enrollment_filter)

        # Guruhsiz faol o'quvchilar (qo'ng'iroqchadagi eslatmadan): ochiq a'zoligi yo'q
        if self.request.GET.get("nogroup") == "1":
            qs = qs.filter(status=Student.Status.ACTIVE).exclude(enrollments__status__in=OPEN_ENROLLMENT_STATUSES)

        # Manba bo'yicha filtr
        source_filter = self.request.GET.get("source", "").strip()
        if source_filter in Student.Source.values:
            qs = qs.filter(source=source_filter)

        # Maktab bo'yicha filtr
        school_filter = self.request.GET.get("school", "").strip()
        if school_filter:
            qs = qs.filter(school__icontains=school_filter)

        # Qora ro'yxat bo'yicha filtr
        blacklist_filter = self.request.GET.get("blacklist", "").strip()
        if blacklist_filter == "yes":
            qs = qs.filter(is_blacklisted=True)
        elif blacklist_filter == "no":
            qs = qs.filter(is_blacklisted=False)

        # Kurs bo'yicha filtr (ochiq a'zoliklar)
        course_id = parse_int(self.request.GET.get("course", ""))
        if course_id is not None:
            course_q = Q(
                enrollments__status__in=OPEN_ENROLLMENT_STATUSES,
                enrollments__group__course_id=course_id,
            )
            if user.role == User.Role.TEACHER:
                course_q &= Q(enrollments__group__teacher__user=user)
            qs = qs.filter(course_q)

        # Umumiy balans: subquery orqali (JOIN dubllari summani buzmasligi uchun)
        balance_sq = (
            LedgerEntry.objects.filter(student=OuterRef("pk"))
            .values("student")
            .annotate(total=Sum("amount"))
            .values("total")
        )
        qs = qs.annotate(
            balance=Coalesce(Subquery(balance_sq, output_field=BigIntegerField()), Value(0))
        )
        if user.role != User.Role.TEACHER:
            payment = self.request.GET.get("payment", "")
            if payment == "debt":
                qs = qs.filter(balance__lt=0)
            elif payment == "ok":
                qs = qs.filter(balance__gte=0)

        # Guruh va o'qituvchi bog'lanishlari sabab dubllar paydo bo'lishining oldini olamiz
        return qs.distinct().order_by("-id")

    def get(self, request, *args, **kwargs):
        if request.GET.get("export") == "xlsx":
            if request.user.role == User.Role.TEACHER:
                raise PermissionDenied("Eksport faqat administrator va direktor uchun.")
            return export_students_xlsx(self.get_queryset())
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context["can_manage_student"] = user_has_role(user, [User.Role.ADMINISTRATOR])
        if user.role == User.Role.TEACHER:
            context["groups"] = Group.objects.filter(
                teacher__user=user, status=Group.Status.ACTIVE
            ).order_by("name")
            context["teachers"] = Teacher.objects.filter(user=user)
        else:
            context["groups"] = Group.objects.filter(
                status=Group.Status.ACTIVE
            ).order_by("name")
            context["teachers"] = Teacher.objects.filter(is_active=True).order_by(
                "full_name"
            )

        context["sources"] = Student.Source.choices
        context["current_q"] = self.request.GET.get("q", "").strip()
        context["current_status"] = self.request.GET.get("status", "").strip()
        context["current_group"] = self.request.GET.get("group", "").strip()
        context["current_teacher"] = self.request.GET.get("teacher", "").strip()
        context["current_source"] = self.request.GET.get("source", "").strip()
        context["current_school"] = self.request.GET.get("school", "").strip()
        context["current_blacklist"] = self.request.GET.get("blacklist", "").strip()
        context["current_course"] = self.request.GET.get("course", "").strip()
        context["current_payment"] = self.request.GET.get("payment", "").strip()
        context["courses"] = Course.objects.filter(is_active=True).order_by("name")

        params = self.request.GET.copy()
        params.pop("page", None)
        params.pop("export", None)
        context["querystring"] = params.urlencode()

        # Sahifadagi o'quvchilar: guruh bo'yicha balans va dars vaqti
        page_students = list(context["students"])
        ids = [st.pk for st in page_students]
        group_balances = {
            (row["student"], row["group"]): row["total"]
            for row in LedgerEntry.objects.filter(student_id__in=ids)
            .values("student", "group")
            .annotate(total=Sum("amount"))
        }
        open_statuses = set(OPEN_ENROLLMENT_STATUSES)
        group_ids = {e.group_id for st in page_students for e in st.enrollments.all()}
        schedules = group_schedule_summaries(group_ids)
        # O'qituvchi o'quvchining boshqa o'qituvchilardagi guruhlarini ko'rmaydi
        only_own = self.request.user.role == User.Role.TEACHER
        for st in page_students:
            st.open_enrollments = [
                e for e in st.enrollments.all()
                if e.status in open_statuses and (not only_own or e.group.teacher.user_id == self.request.user.pk)
            ]
            for e in st.open_enrollments:
                e.group_balance = group_balances.get((st.pk, e.group_id), 0)
                e.schedule = schedules.get(e.group_id, "")
        context["students"] = page_students
        return context

    def render_to_response(self, context, **response_kwargs):
        # HTMX jonli qidiruv va filtr so'rovi kelganda faqat jadval qismini qaytaramiz
        if self.request.headers.get("HX-Request") == "true":
            return render(
                self.request,
                "students/_student_table.html",
                context,
                **response_kwargs,
            )
        return super().render_to_response(context, **response_kwargs)


class StudentDetailView(RoleRequiredMixin, DetailView):
    """O'quvchi kartochkasi: ma'lumotlar, vasiylar, a'zo bo'lgan guruhlar."""

    model = Student
    template_name = "students/student_detail.html"
    context_object_name = "student"
    allowed_roles = [User.Role.ADMINISTRATOR, User.Role.TEACHER, User.Role.ACCOUNTANT]

    def get_queryset(self):
        return Student.objects.prefetch_related(
            "student_guardians__guardian",
            "enrollments__group__course",
            "enrollments__group__teacher",
        )

    def get_object(self, queryset=None):
        student = super().get_object(queryset)
        user = self.request.user
        if user.role == User.Role.TEACHER:
            # O'qituvchi faqat o'z guruhidagi o'quvchi kartasini ko'ra oladi
            has_common_group = student.enrollments.filter(
                group__teacher__user=user
            ).exists()
            if not has_common_group:
                raise PermissionDenied(
                    "O'qituvchi faqat o'z guruhidagi o'quvchilar ma'lumotini ko'ra oladi."
                )
        return student

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["archive_reasons"] = Student.ArchiveReason.choices
        context["guardian_form"] = GuardianForm()

        # Guruh kartalari: jadval, oylik davomat taqvimi (?month=YYYY-MM)
        from apps.schedule.views import MONTH_NAMES, _parse_month

        today = timezone.localdate()
        month_start = _parse_month(self.request.GET.get("month"), today)
        next_month = (month_start + timedelta(days=32)).replace(day=1)
        enrollments = self.object.enrollments.all()
        if self.request.user.role == User.Role.TEACHER:
            # O'qituvchi kartada faqat o'z guruhlarini ko'radi
            enrollments = [e for e in enrollments if e.group.teacher.user_id == self.request.user.pk]
        enrollments = sorted(enrollments, key=lambda e: (e.status == "finished", e.group.name))
        schedules = group_schedule_summaries({e.group_id for e in enrollments})
        for e in enrollments:
            e.schedule = schedules.get(e.group_id, "")
            e.calendar = attendance_calendar(self.object, e, month_start, today)
            e.balance = None
        context["card_enrollments"] = enrollments
        context["month_title"] = f"{MONTH_NAMES[month_start.month - 1]} {month_start.year}"
        context["prev_month"] = (month_start - timedelta(days=1)).strftime("%Y-%m")
        context["next_month"] = next_month.strftime("%Y-%m")
        context["history"] = []
        context["total_balance"] = None
        from apps.attendance.services import get_attendance_stats

        context["attendance_stats"] = get_attendance_stats(self.object)

        # Moliya ma'lumotlari faqat administrator va direktor uchun.
        # O'qituvchiga balans, leder va to'lov formasi serverda hisoblanmaydi.
        context["can_manage_student"] = user_has_role(self.request.user, [User.Role.ADMINISTRATOR])
        context["enrollments_with_balance"] = []
        context["ledger_entries"] = []
        context["payment_form"] = None
        if not user_has_role(self.request.user, [User.Role.ADMINISTRATOR, User.Role.ACCOUNTANT]):
            return context

        from apps.finance.forms import PaymentForm
        from apps.finance.models import LedgerEntry
        from apps.finance.services import get_balance

        # Balans har bir guruh bo'yicha
        enrollments_with_balance = []
        for enrollment in self.object.enrollments.select_related("group__course"):
            balance = get_balance(self.object, enrollment.group)
            enrollments_with_balance.append(
                {
                    "enrollment": enrollment,
                    "balance": balance,
                }
            )
        context["enrollments_with_balance"] = enrollments_with_balance
        balances = {item["enrollment"].pk: item["balance"] for item in enrollments_with_balance}
        for e in context["card_enrollments"]:
            e.balance = balances.get(e.pk, 0)
        context["total_balance"] = sum(balances.values())
        context["history"] = student_history(self.object)

        # "Guruhga qo'shish" oynasi: faol guruhlar va filtrlar uchun kurs/o'qituvchi
        context["enroll_groups"] = (
            Group.objects.filter(status=Group.Status.ACTIVE)
            .select_related("course", "teacher")
            .order_by("name")
        )
        context["courses"] = Course.objects.filter(is_active=True).order_by("name")
        context["teachers"] = Teacher.objects.filter(is_active=True).order_by("full_name")
        context["today"] = today

        # Oxirgi 50 ta ledger yozuvlari (barcha guruhlar bo'yicha)
        context["ledger_entries"] = (
            LedgerEntry.objects.filter(student=self.object)
            .select_related("group", "lesson", "created_by")
            .order_by("-entry_date", "-created_at")[:50]
        )

        # To'lov formasi
        context["payment_form"] = PaymentForm()


        return context


class StudentCreateView(RoleRequiredMixin, CreateView):
    """Yangi o'quvchi qo'shish."""

    model = Student
    form_class = StudentForm
    template_name = "students/student_form.html"
    allowed_roles = [User.Role.ADMINISTRATOR]

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        from django.core.exceptions import ValidationError

        from apps.tenants.billing import check_students_limit

        try:
            check_students_limit(1)  # tarif limiti
        except ValidationError as exc:
            form.add_error(None, exc.message)
            return self.form_invalid(form)
        messages.success(self.request, "O'quvchi muvaffaqiyatli qo'shildi.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy("students:student_detail", kwargs={"pk": self.object.pk})


class StudentUpdateView(RoleRequiredMixin, UpdateView):
    """O'quvchi ma'lumotlarini tahrirlash."""

    model = Student
    form_class = StudentForm
    template_name = "students/student_form.html"
    allowed_roles = [User.Role.ADMINISTRATOR]

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        if "status" in form.changed_data and self.object.status == Student.Status.ARCHIVED:
            # Forma orqali arxivlash ham a'zoliklarni tugatadi (arxivlash sababi o'rnatiladi)
            self.object.change_status(Student.Status.ARCHIVED, self.object.archive_reason, user=self.request.user)
        messages.success(self.request, "O'quvchi ma'lumotlari yangilandi.")
        return response

    def get_success_url(self):
        return reverse_lazy("students:student_detail", kwargs={"pk": self.object.pk})


class StudentArchiveView(RoleRequiredMixin, View):
    """O'quvchini arxivlash yoki arxivdan chiqarish."""

    allowed_roles = [User.Role.ADMINISTRATOR]

    def post(self, request, pk):
        student = get_object_or_404(Student, pk=pk)
        if student.status == Student.Status.ACTIVE:
            student.change_status(
                Student.Status.ARCHIVED, request.POST.get("archive_reason", ""), user=request.user
            )
            messages.success(request, f"{student.full_name} arxivlandi.")
        else:
            student.change_status(Student.Status.ACTIVE, user=request.user)
            messages.success(request, f"{student.full_name} faol holatga qaytarildi.")
        return redirect("students:student_detail", pk=pk)


class GuardianCreateView(RoleRequiredMixin, View):
    """O'quvchi kartasidan to'g'ridan-to'g'ri vasiy (ota-ona) qo'shish."""

    allowed_roles = [User.Role.ADMINISTRATOR]

    def post(self, request, pk):
        student = get_object_or_404(Student, pk=pk)
        form = GuardianForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                guardian = form.save()
                relation = form.cleaned_data.get("relation", "")
                is_primary = form.cleaned_data.get("is_primary", False)
                StudentGuardian.objects.create(
                    student=student,
                    guardian=guardian,
                    relation=relation,
                    is_primary=is_primary,
                )
            messages.success(request, "Vasiy ma'lumotlari muvaffaqiyatli saqlandi.")
        else:
            messages.error(request, "Vasiy ma'lumotlarini saqlashda xatolik yuz berdi.")
        return redirect("students:student_detail", pk=pk)


class StudentStatusView(RoleRequiredMixin, View):
    """O'quvchi holatini o'zgartirish: Faol / Arxivlangan / Muzlatilgan."""

    allowed_roles = [User.Role.ADMINISTRATOR]

    def post(self, request, pk):
        student = get_object_or_404(Student, pk=pk)
        new_status = request.POST.get("status", "")
        if new_status not in Student.Status.values:
            messages.error(request, "Noto'g'ri holat tanlandi.")
        elif new_status != student.status:
            if student.status == Student.Status.ARCHIVED:
                from django.core.exceptions import ValidationError

                from apps.tenants.billing import check_students_limit

                try:
                    check_students_limit(1)  # arxivdan qaytgan o'quvchi yana limitga kiradi
                except ValidationError as exc:
                    messages.error(request, exc.message)
                    return redirect("students:student_detail", pk=pk)
            student.change_status(new_status, request.POST.get("archive_reason", ""), user=request.user)
            messages.success(
                request,
                f"{student.full_name}: holat '{student.get_status_display()}' ga o'zgartirildi.",
            )
        return redirect("students:student_detail", pk=pk)


class ChurnReportView(RoleRequiredMixin, TemplateView):
    """Arxivlangan o'quvchilar: ketish sabablari bo'yicha hisobot (oxirgi 12 oy)."""

    allowed_roles = [User.Role.ADMINISTRATOR]
    template_name = "students/churn_report.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        since = timezone.now() - timedelta(days=365)
        archived = Student.objects.filter(
            status=Student.Status.ARCHIVED, archived_at__gte=since
        )
        labels = dict(Student.ArchiveReason.choices)
        rows = [
            {"label": labels.get(r["archive_reason"], "Ko'rsatilmagan"), "count": r["n"]}
            for r in archived.values("archive_reason")
            .annotate(n=Count("id"))
            .order_by("-n")
        ]
        total = sum(r["count"] for r in rows)
        for r in rows:
            r["percent"] = round(r["count"] * 100 / total) if total else 0
        context.update(rows=rows, total=total)
        return context


class StudentImportView(RoleRequiredMixin, View):
    """O'quvchilarni Excel (.xlsx) fayldan ommaviy import qilish."""

    allowed_roles = [User.Role.ADMINISTRATOR]
    template_name = "students/student_import.html"

    def get(self, request):
        return render(request, self.template_name)

    def post(self, request):
        upload = request.FILES.get("file")
        if upload is None:
            messages.error(request, "Fayl tanlanmadi.")
            return render(request, self.template_name)

        result = importer.import_students(upload)
        if result.created:
            # bulk_create signal chaqirmaydi, shuning uchun jurnalga bitta umumiy yozuv qo'shamiz
            AuditLog.objects.create(
                user=request.user,
                username=request.user.get_username(),
                action=AuditLog.Action.CREATE,
                model="students.Student",
                object_repr=f"Excel import: {result.created} ta o'quvchi",
                ip_address=request.META.get("REMOTE_ADDR"),
            )
            messages.success(request, f"{result.created} ta o'quvchi import qilindi.")
        return render(request, self.template_name, {"result": result})


class StudentImportTemplateView(RoleRequiredMixin, View):
    """Import uchun bo'sh Excel shablonini yuklab olish."""

    allowed_roles = [User.Role.ADMINISTRATOR]

    def get(self, request):
        response = HttpResponse(
            importer.build_template(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = 'attachment; filename="oquvchilar_shablon.xlsx"'
        return response


def export_students_xlsx(queryset):
    """Joriy filtr bo'yicha o'quvchilar ro'yxatini Excel (.xlsx) faylga chiqaradi."""
    from io import BytesIO

    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "O'quvchilar"
    ws.append([
        "ID", "Familiya", "Ism", "Otasining ismi", "Telefon", "Ota-ona telefoni", "Holati",
        "Guruhlar", "Balans (so'm)", "Maktab", "Manba", "Qo'shilgan sana", "Qora ro'yxat",
    ])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    open_statuses = set(OPEN_ENROLLMENT_STATUSES)
    students = queryset.prefetch_related("student_guardians__guardian", "enrollments__group")
    for st in students:
        guardian = next((sg.guardian for sg in st.student_guardians.all() if sg.is_primary), None)
        groups = ", ".join(e.group.name for e in st.enrollments.all() if e.status in open_statuses)
        ws.append(safe_row([
            st.pk, st.last_name, st.first_name, st.middle_name, st.phone,
            guardian.phone if guardian else "", st.get_status_display(), groups,
            st.balance, st.school, st.get_source_display(), st.joined_date,
            "Ha" if st.is_blacklisted else "",
        ]))
    widths = (7, 18, 16, 16, 16, 16, 13, 30, 15, 18, 16, 14, 12)
    for column, width in zip("ABCDEFGHIJKLM", widths, strict=True):
        ws.column_dimensions[column].width = width
    buffer = BytesIO()
    wb.save(buffer)
    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="oquvchilar.xlsx"'
    return response
