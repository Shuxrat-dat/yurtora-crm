"""Finance views."""

import csv
from urllib.parse import urlencode, urlparse

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Prefetch, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.generic import ListView

from apps.accounts.models import User
from apps.accounts.permissions import RoleRequiredMixin
from apps.core.export import SafeCsvWriter
from apps.core.filters import (
    parse_date,
    parse_int,
    resolve_date_range,
    resolve_year_month,
)
from apps.core.http import safe_next_url
from apps.finance import services
from apps.finance.forms import (
    MAX_PAYMENT_AMOUNT,
    ExcusedCreditForm,
    PaymentForm,
    ReceiptSettingsForm,
    RefundForm,
)
from apps.finance.models import LedgerEntry, ReceiptSettings
from apps.finance.services import DuplicatePayment, generate_receipt_qr_svg
from apps.groups.models import Enrollment, Group
from apps.students.models import Student

# To'lov oynasi (yuqoridagi "To'lov" tugmasi): administrator va direktor
PAYMENT_MODAL_ROLES = [User.Role.ADMINISTRATOR, User.Role.DIRECTOR, User.Role.ACCOUNTANT]
MIN_SEARCH_LENGTH = 3
DUPLICATE_MESSAGE = "Bu to'lov allaqachon qabul qilingan: takroriy yuborish e'tiborga olinmadi."


def _idempotency_key(request):
    return request.POST.get("idempotency_key", "").strip()[:64] or None



def period_choices(today, debt_values=(), first_group_debts=()):
    """To'lov oyini tanlash ro'yxati: eng eski qarz oyidan (yoki 3 oy oldindan) 2 oy keyingacha. Standart: birinchi guruhdagi eng eski qarz oyi, bo'lmasa joriy oy."""
    from apps.core.months import (
        add_months,
        month_label,
        month_start,
        month_value,
        parse_month,
    )

    current = month_start(today)
    start = min([add_months(current, -3), *[parse_month(v) for v in debt_values]])
    start = max(start, add_months(current, -24))
    options, month = [], start
    while month <= add_months(current, 2):
        options.append({"value": month_value(month), "label": month_label(month)})
        month = add_months(month, 1)
    default = first_group_debts[0]["value"] if first_group_debts else month_value(current)
    return options, default


OPEN_ENROLLMENT_STATUSES = [
    Enrollment.Status.ACTIVE,
    Enrollment.Status.TRIAL,
    Enrollment.Status.FROZEN,
]


class PaymentCreateView(RoleRequiredMixin, View):
    """To'lovni ro'yxatga olish."""

    allowed_roles = [User.Role.ADMINISTRATOR, User.Role.DIRECTOR, User.Role.ACCOUNTANT]

    def post(self, request, student_id, group_id):
        student = get_object_or_404(Student, pk=student_id)
        group = get_object_or_404(Group, pk=group_id)
        form = PaymentForm(request.POST)

        if not Enrollment.objects.filter(
            student=student, group=group, status__in=OPEN_ENROLLMENT_STATUSES
        ).exists():
            messages.error(request, "O'quvchi tanlangan guruhga a'zo emas.")
        elif form.is_valid():
            try:
                services.register_payment(
                    student=student,
                    group=group,
                    amount=form.cleaned_data["amount"],
                    method=form.cleaned_data["method"],
                    entry_date=form.cleaned_data["entry_date"],
                    period=form.cleaned_data.get("period"),
                    comment=form.cleaned_data.get("comment", ""),
                    user=request.user,
                    idempotency_key=_idempotency_key(request),
                )
                messages.success(
                    request,
                    f"{student.full_name} uchun {form.cleaned_data['amount']:,} so'm to'lov qabul qilindi.",
                )
            except DuplicatePayment:
                messages.info(request, DUPLICATE_MESSAGE)
            except (ValidationError, PermissionDenied) as e:
                messages.error(request, str(e))
        else:
            messages.error(request, "Forma ma'lumotlari noto'g'ri kiritildi.")

        # Qaytish URL
        next_url = safe_next_url(request)
        if next_url:
            return redirect(next_url)
        return redirect("students:student_detail", pk=student_id)


class ExcusedCreditView(RoleRequiredMixin, View):
    """Uzrli sabab: kreditlash (charge ni excused_credit orqali qaytarish). Faqat direktor."""

    allowed_roles = [User.Role.DIRECTOR]

    def post(self, request, charge_id):
        charge = get_object_or_404(LedgerEntry, pk=charge_id, kind=LedgerEntry.Kind.CHARGE)
        form = ExcusedCreditForm(request.POST)

        if form.is_valid():
            try:
                services.grant_excused_credit(
                    charge=charge,
                    user=request.user,
                    comment=form.cleaned_data["comment"],
                )
                messages.success(request, "Uzrli sabab bo'yicha kredit berildi.")
            except (ValidationError, PermissionDenied) as e:
                messages.error(request, str(e))
        else:
            messages.error(request, "Izoh majburiy.")

        next_url = safe_next_url(request)
        if next_url:
            return redirect(next_url)
        return redirect("students:student_detail", pk=charge.student.pk)


def _filtered_debtors(request):
    """Qarzdorlar: guruh va qidiruv filtrlari bazada qo'llanadi (ro'yxat va CSV uchun bir xil)."""
    return services.get_debtors(
        group=parse_int(request.GET.get("group")),
        search=request.GET.get("q", ""),
    )


class DebtorsListView(RoleRequiredMixin, ListView):
    """Qarzdorlar ro'yxati (N+1 muammosiz, pagination bilan)."""

    template_name = "finance/debtors_list.html"
    context_object_name = "debtors"
    paginate_by = 50
    allowed_roles = [User.Role.ACCOUNTANT]

    def get_queryset(self):
        return _filtered_debtors(self.request)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        page_rows = list(context["debtors"])
        months = services.debt_months_map((d["student"].pk, d["group"].pk) for d in page_rows)
        for row in page_rows:
            row["months"] = months.get((row["student"].pk, row["group"].pk), [])
        context["debtors"] = page_rows
        group_id = parse_int(self.request.GET.get("group"))
        params = self.request.GET.copy()
        params.pop("page", None)  # sahifa havolalarida "page" ikki marta takrorlanmasligi uchun
        context["groups"] = Group.objects.filter(status=Group.Status.ACTIVE).order_by("name")
        context["current_group"] = "" if group_id is None else str(group_id)
        context["current_q"] = self.request.GET.get("q", "")
        context["total_debt"] = sum(d["balance"] for d in self.object_list)
        context["querystring"] = params.urlencode()
        return context


class DebtorsExportView(RoleRequiredMixin, View):
    """Qarzdorlarni CSV formatida eksport qilish."""

    allowed_roles = [User.Role.ACCOUNTANT]

    def get(self, request):
        debtors = _filtered_debtors(request)

        # CSV generatsiya
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="qarzdorlar.csv"'
        response.write("\ufeff")  # UTF-8 BOM for Excel

        writer = SafeCsvWriter(csv.writer(response))
        writer.writerow(
            ["O'quvchi", "Guruh", "Qarz (so'm)", "Telefon", "Ota-ona telefoni"]
        )

        for d in debtors:
            student = d["student"]
            group = d["group"]
            balance = d["balance"]

            # Ota-ona telefoni
            parent_phone = ""
            if student:
                primary_guardian = student.student_guardians.filter(is_primary=True).first()
                if primary_guardian:
                    parent_phone = primary_guardian.guardian.phone

            writer.writerow(
                [
                    student.full_name if student else "",
                    group.name if group else "",
                    abs(balance),  # musbat qilib ko'rsatish
                    student.phone if student else "",
                    parent_phone,
                ]
            )

        return response


class DailyCashReportView(RoleRequiredMixin, View):
    """Kunlik kassa hisoboti (to'lovlar bo'yicha)."""

    template_name = "finance/daily_cash.html"
    allowed_roles = [User.Role.ACCOUNTANT]

    def get(self, request):
        # Sanalar: bugun Asia/Tashkent bo'yicha; chegaralar kiritilgan kunni o'z ichiga oladi
        from django.utils import timezone

        today = timezone.localdate()
        date_from, date_to = resolve_date_range(
            request.GET.get("date_from", ""), request.GET.get("date_to", ""), today, today
        )

        # To'lovlar
        payments = (
            LedgerEntry.objects.filter(
                kind__in=services.INCOME_KINDS, entry_date__gte=date_from, entry_date__lte=date_to
            )
            .select_related("student", "group", "created_by")
        )

        # To'lov usuli bo'yicha filtr
        method_filter = request.GET.get("method", "").strip()
        if method_filter in [m[0] for m in LedgerEntry.PaymentMethod.choices]:
            payments = payments.filter(method=method_filter)

        payments = payments.order_by("-entry_date", "-created_at")

        # To'lov usullari bo'yicha yig'indi (bitta so'rov bilan)
        totals_by_method = {
            row["method"]: row["total"] or 0
            for row in payments.order_by().values("method").annotate(total=Sum("amount"))
        }
        method_totals = {
            label: totals_by_method.get(code, 0)
            for code, label in LedgerEntry.PaymentMethod.choices
        }
        total_amount = sum(totals_by_method.values())

        context = {
            "date_from": date_from,
            "date_to": date_to,
            "payments": payments,
            "method_totals": method_totals,
            "total_amount": total_amount,
            "current_date_from": date_from.isoformat(),
            "current_date_to": date_to.isoformat(),
            "current_method": method_filter,
            "payment_methods": LedgerEntry.PaymentMethod.choices,
        }
        return render(request, self.template_name, context)



class FinanceDashboardView(RoleRequiredMixin, View):
    """Moliya boshqaruv shtabi: barcha hisobotlar, oylik reja, grafiklar va dinamika."""

    template_name = "finance/dashboard.html"
    allowed_roles = [User.Role.DIRECTOR]

    def get(self, request):
        import json
        from calendar import monthrange
        from datetime import date

        from django.db.models import Sum
        from django.utils import timezone

        from apps.finance.models import (
            ExpenseCategory,
            ExpenseItem,
            PayrollAdjustment,
            PayrollLine,
            PayrollMonth,
            SalaryPayout,
        )
        from apps.groups.models import Enrollment, Group
        from apps.students.models import Student

        today = timezone.localdate()

        # Filtrlash parametrlari (noto'g'ri qiymatlar xato bermaydi)
        date_from_str = request.GET.get("date_from", "")
        date_to_str = request.GET.get("date_to", "")
        method_filter = request.GET.get("method", "").strip()

        year, month = resolve_year_month(
            request.GET.get("year"), request.GET.get("month"), today
        )
        _, last_day = monthrange(year, month)

        # Sana oralig'i berilmasa tanlangan oy olinadi; bitta chegara berilsa
        # ikkinchisi oydan to'ldiriladi; teskari oraliq almashtiriladi.
        start_date, end_date = resolve_date_range(
            date_from_str, date_to_str, date(year, month, 1), date(year, month, last_day)
        )
        # Formada faqat to'g'ri kiritilgan sanalar qoldiriladi
        if parse_date(date_from_str) is None:
            date_from_str = ""
        if parse_date(date_to_str) is None:
            date_to_str = ""

        # 1. Sof tushum: to'lovlar va qaytarilgan pullar (LedgerEntry kind=payment/refund)
        payments_qs = LedgerEntry.objects.filter(
            kind__in=services.INCOME_KINDS,
            entry_date__gte=start_date,
            entry_date__lte=end_date,
        )
        if method_filter in [m[0] for m in LedgerEntry.PaymentMethod.choices]:
            payments_qs = payments_qs.filter(method=method_filter)

        total_income = payments_qs.aggregate(total=Sum("amount"))["total"] or 0

        # To'lov usullari bo'yicha (Donut chart & ro'yxat)
        donut_labels = []
        donut_series = []
        payment_methods_stat = []

        for method_code, method_label in LedgerEntry.PaymentMethod.choices:
            amt = (
                payments_qs.filter(method=method_code).aggregate(total=Sum("amount"))["total"]
                or 0
            )
            pct = round((amt / total_income * 100), 1) if total_income > 0 else 0
            donut_labels.append(str(method_label))
            donut_series.append(int(amt))
            payment_methods_stat.append(
                {
                    "code": method_code,
                    "label": method_label,
                    "amount": amt,
                    "percent": pct,
                }
            )

        # 2. Chiqimlar (SalaryPayout + ExpenseItem)
        salary_paid = (
            SalaryPayout.objects.filter(paid_date__gte=start_date, paid_date__lte=end_date)
            .aggregate(total=Sum("amount"))["total"]
            or 0
        )
        expenses_qs = ExpenseItem.objects.filter(
            category__category_type=ExpenseCategory.Type.EXPENSE,
            entry_date__gte=start_date,
            entry_date__lte=end_date,
        )
        extra_expenses = expenses_qs.aggregate(total=Sum("amount"))["total"] or 0
        total_expenses = salary_paid + extra_expenses

        # Chiqim hisoboti: bo'limlar bo'yicha guruhlash
        expense_categories = ExpenseCategory.objects.filter(
            category_type=ExpenseCategory.Type.EXPENSE
        )
        expense_report = []
        for cat in expense_categories:
            cat_sum = (
                expenses_qs.filter(category=cat).aggregate(total=Sum("amount"))["total"] or 0
            )
            expense_report.append(
                {
                    "id": cat.id,
                    "name": cat.name,
                    "amount": cat_sum,
                }
            )

        # Kirim hisoboti (qo'shimcha kirimlar)
        income_items_qs = ExpenseItem.objects.filter(
            category__category_type=ExpenseCategory.Type.INCOME,
            entry_date__gte=start_date,
            entry_date__lte=end_date,
        )
        income_categories = ExpenseCategory.objects.filter(
            category_type=ExpenseCategory.Type.INCOME
        )
        income_report = []
        for cat in income_categories:
            c_sum = (
                income_items_qs.filter(category=cat).aggregate(total=Sum("amount"))["total"]
                or 0
            )
            if c_sum > 0:
                income_report.append({"id": cat.id, "name": cat.name, "amount": c_sum})

        # 3. Foyda
        net_profit = total_income - total_expenses

        # 4. Aktiv balans (Jami tushumlar - jami chiqimlar butun davr bo'yicha)
        all_time_income = (
            LedgerEntry.objects.filter(kind__in=services.INCOME_KINDS).aggregate(
                total=Sum("amount")
            )["total"]
            or 0
        )
        all_time_salary = SalaryPayout.objects.aggregate(total=Sum("amount"))["total"] or 0
        all_time_extra_expenses = (
            ExpenseItem.objects.filter(
                category__category_type=ExpenseCategory.Type.EXPENSE
            ).aggregate(total=Sum("amount"))["total"]
            or 0
        )
        active_balance = all_time_income - (all_time_salary + all_time_extra_expenses)

        # 5. LTV (Lifetime Value)
        # Jami tushum / (barcha to'lov qilgan o'quvchilar soni)
        paying_students_count = (
            LedgerEntry.objects.filter(kind=LedgerEntry.Kind.PAYMENT)
            .values("student")
            .distinct()
            .count()
        )
        ltv = int(all_time_income / paying_students_count) if paying_students_count > 0 else 0

        # 6. CAC (Customer Acquisition Cost) & Marketing samaradorligi
        marketing_cat = ExpenseCategory.objects.filter(
            name__icontains="marketing", category_type=ExpenseCategory.Type.EXPENSE
        ).first()
        marketing_expense = (
            expenses_qs.filter(category=marketing_cat).aggregate(total=Sum("amount"))["total"]
            or 0
            if marketing_cat
            else 0
        )
        new_students_month = Student.objects.filter(
            joined_date__gte=start_date, joined_date__lte=end_date
        ).count()
        cac = (
            int(marketing_expense / new_students_month)
            if new_students_month > 0 and marketing_expense > 0
            else 0
        )
        marketing_efficiency = (
            round((total_income / marketing_expense * 100), 1)
            if marketing_expense > 0
            else 0
        )

        # 7. O'rtacha to'lov (Average transaction)
        payments_count = payments_qs.count()
        avg_payment = int(total_income / payments_count) if payments_count > 0 else 0

        # 8. Yillik aylanmalar grafigi (12 oy bo'yicha)
        year_months = [
            "Yan", "Fev", "Mart", "Apr", "May", "Iyun",
            "Iyul", "Avg", "Sen", "Okt", "Noy", "Dek"
        ]
        chart_income = []
        chart_expenses = []
        chart_profit = []

        # Yil bo'yicha oylik yig'indilar: har bir qator uchun 12 ta emas, bitta guruhlangan so'rov
        from django.db.models.functions import ExtractMonth

        def _by_month(queryset, date_field):
            rows = (
                queryset.annotate(m=ExtractMonth(date_field)).order_by().values("m").annotate(total=Sum("amount"))
            )
            return {row["m"]: row["total"] or 0 for row in rows}

        year_income = _by_month(
            LedgerEntry.objects.filter(kind__in=services.INCOME_KINDS, entry_date__year=year), "entry_date"
        )
        year_salary = _by_month(SalaryPayout.objects.filter(paid_date__year=year), "paid_date")
        year_extra = _by_month(
            ExpenseItem.objects.filter(
                category__category_type=ExpenseCategory.Type.EXPENSE, entry_date__year=year
            ),
            "entry_date",
        )
        for m in range(1, 13):
            m_inc = year_income.get(m, 0)
            m_exp = year_salary.get(m, 0) + year_extra.get(m, 0)
            chart_income.append(int(m_inc))
            chart_expenses.append(int(m_exp))
            chart_profit.append(int(m_inc - m_exp))

        # 9. Oylik reja va ko'rsatkichlar
        active_enrollments = Enrollment.objects.filter(
            status=Enrollment.Status.ACTIVE
        ).select_related("group", "group__course")

        monthly_plan = (
            sum(
                e.effective_price
                for e in active_enrollments
                if e.group and e.group.course and e.effective_price
            )
            or 0
        )

        achieved_percent = (
            round((total_income / monthly_plan * 100), 1) if monthly_plan > 0 else 0
        )
        remaining_plan = max(0, monthly_plan - total_income)
        remaining_percent = (
            round((remaining_plan / monthly_plan * 100), 1) if monthly_plan > 0 else 0
        )

        # Qarzdorlik va oldindan to'lovlar (Haqiqiy student balanslari)
        student_balances = (
            LedgerEntry.objects.values("student")
            .annotate(balance=Sum("amount"))
        )
        active_debt = sum(-b["balance"] for b in student_balances if b["balance"] and b["balance"] < 0)
        prepayments = sum(b["balance"] for b in student_balances if b["balance"] and b["balance"] > 0)

        # Joriy oy ulushiga (shu oydagi charge'lar)
        current_month_charges = abs(
            LedgerEntry.objects.filter(
                kind=LedgerEntry.Kind.CHARGE,
                entry_date__gte=start_date,
                entry_date__lte=end_date,
            ).aggregate(total=Sum("amount"))["total"]
            or 0
        )

        # 10. Bonus va Jarimalar (PayrollAdjustment)
        p_month = PayrollMonth.objects.filter(year=year, month=month).first()
        adjustments_qs = (
            PayrollAdjustment.objects.filter(month=p_month)
            if p_month
            else PayrollAdjustment.objects.none()
        )
        total_bonuses = (
            adjustments_qs.filter(amount__gt=0).aggregate(total=Sum("amount"))["total"] or 0
        )
        total_penalties = abs(
            adjustments_qs.filter(amount__lt=0).aggregate(total=Sum("amount"))["total"] or 0
        )

        # 11. Ish haqi hisobotlari jadvali (oxirgi 12 oy tarixi)
        payroll_history = []
        payroll_months = {(pm.year, pm.month): pm for pm in PayrollMonth.objects.filter(year__in=[year, year - 1])}
        for back_m in range(0, 12):
            hist_m = ((month - 1 - back_m) % 12) + 1
            hist_y = year - ((back_m + (12 - month)) // 12) if month <= back_m else year
            m_name = year_months[hist_m - 1]

            pm = payroll_months.get((hist_y, hist_m))
            if pm and pm.is_closed:
                p_lines = PayrollLine.objects.filter(month=pm)
                staff_count = p_lines.values("teacher").distinct().count()
                doimiy = (
                    p_lines.filter(kind=PayrollLine.Kind.FIXED).aggregate(
                        total=Sum("amount")
                    )["total"]
                    or 0
                )
                foiz = (
                    p_lines.filter(kind=PayrollLine.Kind.PERCENT).aggregate(
                        total=Sum("amount")
                    )["total"]
                    or 0
                )
                bon = (
                    PayrollAdjustment.objects.filter(month=pm, amount__gt=0).aggregate(
                        total=Sum("amount")
                    )["total"]
                    or 0
                )
                jar = (
                    PayrollAdjustment.objects.filter(month=pm, amount__lt=0).aggregate(
                        total=Sum("amount")
                    )["total"]
                    or 0
                )
                payouts_sum = (
                    SalaryPayout.objects.filter(month=pm).aggregate(total=Sum("amount"))[
                        "total"
                    ]
                    or 0
                )
                jami = doimiy + foiz + bon + jar
            else:
                p_data = services.compute_payroll(hist_y, hist_m)
                staff_count = len(p_data)
                doimiy = sum(d["fixed_salary"] or 0 for d in p_data.values())
                foiz = sum(
                    sum(g["amount"] for g in d.get("groups", [])) for d in p_data.values()
                )
                bon = 0
                jar = 0
                payouts_sum = sum(d.get("total_paid", 0) for d in p_data.values())
                jami = sum(d.get("total_accrued", 0) for d in p_data.values())

            if staff_count > 0 or jami > 0 or hist_m == month:
                payroll_history.append(
                    {
                        "index": len(payroll_history) + 1,
                        "year": hist_y,
                        "month": hist_m,
                        "month_name": f"{m_name} {hist_y}",
                        "staff_count": staff_count,
                        "doimiy": doimiy,
                        "foiz": foiz,
                        "bonus": bon,
                        "jarima": jar,
                        "avans": payouts_sum,
                        "jami": jami,
                    }
                )

        month_names_uz = [
            (1, "Yanvar"), (2, "Fevral"), (3, "Mart"), (4, "Aprel"),
            (5, "May"), (6, "Iyun"), (7, "Iyul"), (8, "Avgust"),
            (9, "Sentabr"), (10, "Oktabr"), (11, "Noyabr"), (12, "Dekabr")
        ]

        # Tezkor to'lov modal formasi uchun talabalar va guruhlar
        quick_students = Student.objects.exclude(is_blacklisted=True).order_by("first_name")[:100]
        quick_groups = Group.objects.filter(status=Group.Status.ACTIVE).select_related("course").order_by("name")

        context = {
            "year": year,
            "month": month,
            "start_date": start_date,
            "end_date": end_date,
            "date_from": date_from_str,
            "date_to": date_to_str,
            "current_method": method_filter,
            "total_income": total_income,
            "total_expenses": total_expenses,
            "salary_paid": salary_paid,
            "extra_expenses": extra_expenses,
            "net_profit": net_profit,
            "active_balance": active_balance,
            "ltv": ltv,
            "cac": cac,
            "marketing_efficiency": marketing_efficiency,
            "avg_payment": avg_payment,
            "payment_methods_stat": payment_methods_stat,
            "donut_labels_json": json.dumps(donut_labels),
            "donut_series_json": json.dumps(donut_series),
            "chart_months_json": json.dumps(year_months),
            "chart_income_json": json.dumps(chart_income),
            "chart_expenses_json": json.dumps(chart_expenses),
            "chart_profit_json": json.dumps(chart_profit),
            "monthly_plan": monthly_plan,
            "achieved_percent": achieved_percent,
            "remaining_plan": remaining_plan,
            "remaining_percent": remaining_percent,
            "active_debt": active_debt,
            "prepayments": prepayments,
            "current_month_charges": current_month_charges,
            "expense_report": expense_report,
            "income_report": income_report,
            "total_bonuses": total_bonuses,
            "total_penalties": total_penalties,
            "payroll_history": payroll_history,
            "all_payment_methods": LedgerEntry.PaymentMethod.choices,
            "month_names_uz": month_names_uz,
            "expense_categories": expense_categories,
            "quick_students": quick_students,
            "quick_groups": quick_groups,
            "available_years": [year - 2, year - 1, year, year + 1],
        }
        return render(request, self.template_name, context)


class PayrollView(RoleRequiredMixin, View):
    """Oylik hisob-kitob: o'qituvchilar, foizlar, to'lovlar."""

    template_name = "finance/payroll.html"
    allowed_roles = [User.Role.DIRECTOR]

    def get(self, request):

        from django.utils import timezone

        # Oy tanlash
        today = timezone.localdate()
        year, month = resolve_year_month(
            request.GET.get("year"), request.GET.get("month"), today
        )

        # Hisoblash
        payroll_data = services.compute_payroll(year, month)

        # Oy holati
        from apps.finance.models import PayrollMonth

        try:
            payroll_month = PayrollMonth.objects.get(year=year, month=month)
            is_closed = payroll_month.is_closed
        except PayrollMonth.DoesNotExist:
            is_closed = False

        context = {
            "year": year,
            "month": month,
            "payroll_data": payroll_data,
            "is_closed": is_closed,
            "missing_percent": [
                {"teacher": data["teacher"], "row": row}
                for data in payroll_data.values()
                for row in data.get("missing_percent", [])
            ],
        }
        return render(request, self.template_name, context)


class ClosePayrollMonthView(RoleRequiredMixin, View):
    """Oyni yopish (faqat director)."""

    allowed_roles = [User.Role.DIRECTOR]

    def post(self, request):
        from django.utils import timezone

        year, month = resolve_year_month(request.POST.get("year"), request.POST.get("month"), timezone.localdate())

        try:
            services.close_payroll_month(year, month, request.user)
            messages.success(request, f"Oy {year}-{month:02d} muvaffaqiyatli yopildi.")
        except (ValidationError, PermissionDenied) as e:
            messages.error(request, str(e))

        query = urlencode({"year": year, "month": month})
        return redirect(f"{reverse('finance:payroll')}?{query}")


class PayrollExportView(RoleRequiredMixin, View):
    """Oylik hisob-kitobni CSV formatida eksport qilish."""

    allowed_roles = [User.Role.DIRECTOR]

    def get(self, request):

        from django.utils import timezone

        today = timezone.localdate()
        year, month = resolve_year_month(
            request.GET.get("year"), request.GET.get("month"), today
        )

        payroll_data = services.compute_payroll(year, month)

        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="ish_haqi_{year}_{month:02d}.csv"'
        response.write("\ufeff")

        writer = SafeCsvWriter(csv.writer(response))
        writer.writerow(
            ["O'qituvchi", "Turi", "Asos", "Foiz", "Hisoblangan", "To'langan", "Qoldiq"]
        )

        for _teacher_id, data in payroll_data.items():
            teacher = data["teacher"]
            if data["pay_type"] == "percent":
                for group_data in data["groups"]:
                    writer.writerow(
                        [
                            teacher.full_name,
                            f"{group_data['group'].name} (foiz)",
                            group_data["base"],
                            "sozlanmagan" if group_data["percent"] is None else f"{group_data['percent']}%",
                            group_data["amount"],
                            "",
                            "",
                        ]
                    )
            else:
                writer.writerow(
                    [
                        teacher.full_name,
                        "Belgilangan oylik",
                        data["fixed_salary"],
                        "",
                        data["fixed_salary"],
                        "",
                        "",
                    ]
                )

            # Jami qator
            writer.writerow(
                [
                    f"  JAMI: {teacher.full_name}",
                    "",
                    "",
                    "",
                    data["total_accrued"],
                    data["total_paid"],
                    data["balance"],
                ]
            )

        return response


class MySalaryView(RoleRequiredMixin, View):
    """O'qituvchi ish haqi ko'rinishi. Faqat direktor (ФОТ faqat direktor uchun)."""

    template_name = "finance/my_salary.html"
    allowed_roles = [User.Role.DIRECTOR]

    def get(self, request):

        from django.utils import timezone

        from apps.teachers.models import Teacher

        teacher_id = request.GET.get("teacher_id")
        if teacher_id:
            teacher = get_object_or_404(Teacher, pk=teacher_id)
        else:
            messages.info(request, "O'qituvchini tanlang.")
            return redirect("teachers:teacher_list")

        # Oy tanlash
        today = timezone.localdate()
        year, month = resolve_year_month(
            request.GET.get("year"), request.GET.get("month"), today
        )

        # Hisoblash (faqat shu o'qituvchi uchun)
        payroll_data = services.compute_payroll(year, month)
        teacher_data = payroll_data.get(teacher.pk, None)

        context = {
            "year": year,
            "month": month,
            "teacher": teacher,
            "teacher_data": teacher_data,
        }
        return render(request, self.template_name, context)


class CategoryCreateView(RoleRequiredMixin, View):
    """Moliya xarajat/kirim bo'limini qo'shish."""

    allowed_roles = [User.Role.DIRECTOR]

    def post(self, request):
        from apps.finance.models import ExpenseCategory

        name = request.POST.get("name", "").strip()
        cat_type = request.POST.get("category_type", ExpenseCategory.Type.EXPENSE)

        if name:
            ExpenseCategory.objects.get_or_create(
                name=name,
                defaults={"category_type": cat_type, "is_system": False},
            )
            messages.success(request, f"'{name}' bo'limi muvaffaqiyatli qo'shildi.")
        else:
            messages.error(request, "Bo'lim nomi bo'sh bo'lishi mumkin emas.")

        return redirect("finance:dashboard")


class ExpenseItemCreateView(RoleRequiredMixin, View):
    """Xarajat yoki kirim yozuvini qo'shish."""

    allowed_roles = [User.Role.DIRECTOR]

    def post(self, request):
        from django.utils import timezone

        from apps.finance.models import ExpenseCategory, ExpenseItem

        category_id = parse_int(request.POST.get("category_id")) or 0
        amount = parse_int(request.POST.get("amount"), 1, MAX_PAYMENT_AMOUNT) or 0
        entry_date = parse_date(request.POST.get("entry_date", "")) or timezone.localdate()
        comment = request.POST.get("comment", "").strip()

        category = get_object_or_404(ExpenseCategory, pk=category_id)

        if amount <= 0:
            messages.error(request, "Summani to'g'ri kiriting (musbat butun son).")
        else:
            ExpenseItem.objects.create(
                category=category,
                amount=amount,
                entry_date=entry_date,
                comment=comment,
                created_by=request.user,
            )
            messages.success(request, f"{category.name} bo'yicha {amount:,} so'm yozuv saqlandi.")

        return redirect("finance:dashboard")


class QuickPaymentView(RoleRequiredMixin, View):
    """Moliya shtabidan to'g'ridan-to'g'ri tezkor to'lov qabul qilish (faqat director)."""

    allowed_roles = [User.Role.DIRECTOR]

    def post(self, request):
        student_id = request.POST.get("student_id", "")
        group_id = request.POST.get("group_id", "")
        student = get_object_or_404(Student, pk=int(student_id) if student_id.isdigit() else 0)
        enrollment = (
            Enrollment.objects.filter(
                student=student,
                group_id=int(group_id) if group_id.isdigit() else 0,
                status__in=OPEN_ENROLLMENT_STATUSES,
            )
            .select_related("group")
            .first()
        )
        form = PaymentForm(request.POST)

        if enrollment is None:
            messages.error(request, "O'quvchi tanlangan guruhga a'zo emas.")
        elif not form.is_valid():
            messages.error(request, "Forma ma'lumotlari noto'g'ri kiritildi: summa, usul va sanani tekshiring.")
        else:
            try:
                services.register_payment(
                    student=student,
                    group=enrollment.group,
                    amount=form.cleaned_data["amount"],
                    method=form.cleaned_data["method"],
                    entry_date=form.cleaned_data["entry_date"],
                    period=form.cleaned_data.get("period"),
                    comment=form.cleaned_data.get("comment", "").strip(),
                    user=request.user,
                    idempotency_key=_idempotency_key(request),
                )
                messages.success(
                    request,
                    f"{student.full_name} uchun {form.cleaned_data['amount']:,} so'm to'lov qabul qilindi.",
                )
            except DuplicatePayment:
                messages.info(request, DUPLICATE_MESSAGE)
            except (ValidationError, PermissionDenied) as e:
                messages.error(request, str(e))

        return redirect("finance:dashboard")


class PaymentReceiptView(RoleRequiredMixin, View):
    """To'lov chekini ko'rish va chop etish (administrator va director uchun)."""

    allowed_roles = [User.Role.ADMINISTRATOR, User.Role.DIRECTOR, User.Role.ACCOUNTANT]
    template_name = "finance/receipt.html"

    def get(self, request, entry_id):
        entry = get_object_or_404(
            LedgerEntry.objects.select_related(
                "student", "group", "group__course", "group__teacher", "created_by"
            ),
            pk=entry_id,
            kind=LedgerEntry.Kind.PAYMENT,
        )

        settings_obj = ReceiptSettings.get_settings()
        # QR faqat chek manzilini saqlaydi (print/next parametrlarisiz)
        receipt_url = request.build_absolute_uri(request.path)
        qr_svg = generate_receipt_qr_svg(receipt_url)

        context = {
            "entry": entry,
            "student": entry.student,
            "group": entry.group,
            "course": entry.group.course if entry.group else None,
            "teacher": entry.group.teacher if entry.group else None,
            "settings": settings_obj,
            "qr_svg": qr_svg,
            # "Saqlash va chek chiqarish": sahifa ochilishi bilan chop etish oynasi chiqadi
            "auto_print": request.GET.get("print") == "1",
            "back_url": safe_next_url(request),
        }
        return render(request, self.template_name, context)


class ReceiptSettingsView(RoleRequiredMixin, View):
    """To'lov cheki sozlamalari: ko'rish, tahrirlash va jonli ko'rinish."""

    allowed_roles = [User.Role.ADMINISTRATOR, User.Role.DIRECTOR]
    template_name = "finance/receipt_settings.html"

    def get(self, request):
        settings_obj = ReceiptSettings.get_settings()
        form = ReceiptSettingsForm(instance=settings_obj)
        sample_url = request.build_absolute_uri(reverse("finance:receipt_settings"))
        preview_qr_svg = generate_receipt_qr_svg(sample_url)

        context = {
            "form": form,
            "settings": settings_obj,
            "preview_qr_svg": preview_qr_svg,
        }
        return render(request, self.template_name, context)

    def post(self, request):
        settings_obj = ReceiptSettings.get_settings()
        form = ReceiptSettingsForm(request.POST, request.FILES, instance=settings_obj)
        if form.is_valid():
            form.save()
            messages.success(request, "Chek sozlamalari muvaffaqiyatli saqlandi.")
            return redirect("finance:receipt_settings")

        sample_url = request.build_absolute_uri(reverse("finance:receipt_settings"))
        preview_qr_svg = generate_receipt_qr_svg(sample_url)
        context = {
            "form": form,
            "settings": settings_obj,
            "preview_qr_svg": preview_qr_svg,
        }
        return render(request, self.template_name, context)


class PaymentStudentSearchView(RoleRequiredMixin, View):
    """To'lov oynasi: o'quvchini familiya/ism bo'yicha qidirish.

    Kamida 3 ta harf kiritilmaguncha hech narsa qaytarilmaydi (bo'sh javob).
    """

    allowed_roles = PAYMENT_MODAL_ROLES
    template_name = "finance/_payment_search_results.html"

    def get(self, request):
        tokens = request.GET.get("q", "").split()
        if len("".join(tokens)) < MIN_SEARCH_LENGTH:
            return HttpResponse("")

        condition = Q()
        for token in tokens:
            condition &= (
                Q(last_name__istartswith=token)
                | Q(first_name__istartswith=token)
                | Q(middle_name__istartswith=token)
            )

        students = (
            Student.objects.filter(condition)
            .exclude(status=Student.Status.ARCHIVED)
            .prefetch_related(
                Prefetch(
                    "enrollments",
                    queryset=Enrollment.objects.filter(
                        status__in=OPEN_ENROLLMENT_STATUSES
                    ).select_related("group"),
                    to_attr="open_enrollments",
                )
            )
            .order_by("last_name", "first_name")[:10]
        )
        return render(request, self.template_name, {"students": students})


class PaymentModalFormView(RoleRequiredMixin, View):
    """To'lov oynasi: tanlangan o'quvchi uchun to'lov formasi (HTMX qismi)."""

    allowed_roles = PAYMENT_MODAL_ROLES
    template_name = "finance/_payment_modal_form.html"

    def get(self, request, student_id):
        from django.utils import timezone

        student = get_object_or_404(Student, pk=student_id)
        enrollments = (
            Enrollment.objects.filter(
                student=student, status__in=OPEN_ENROLLMENT_STATUSES
            )
            .select_related("group")
            .order_by("group__name")
        )
        debts = services.debt_months_map((student.pk, e.group_id) for e in enrollments)
        items = [
            {
                "group": enrollment.group,
                "status": enrollment.get_status_display(),
                "balance": services.get_balance(student, enrollment.group),
                "debt_months": debts.get((student.pk, enrollment.group_id), []),
            }
            for enrollment in enrollments
        ]
        today = timezone.localdate()
        period_options, default_period = period_choices(
            today, [m["value"] for it in items for m in it["debt_months"]], items[0]["debt_months"] if items else []
        )
        # Qaysi sahifada turgan bo'lsa, shu sahifaga qaytamiz (faqat yo'l)
        next_url = urlparse(request.headers.get("HX-Current-URL", "")).path
        context = {
            "student": student,
            "items": items,
            "methods": LedgerEntry.PaymentMethod.choices,
            "today": today,
            "next_url": next_url,
            "period_options": period_options,
            "default_period": default_period,
        }
        return render(request, self.template_name, context)


class PaymentModalCreateView(RoleRequiredMixin, View):
    """To'lov oynasidan to'lovni saqlash (administrator va direktor)."""

    allowed_roles = PAYMENT_MODAL_ROLES

    def post(self, request):
        student_id = request.POST.get("student_id", "")
        group_id = request.POST.get("group_id", "")
        student = get_object_or_404(
            Student, pk=int(student_id) if student_id.isdigit() else 0
        )

        enrollment = None
        if group_id.isdigit():
            enrollment = (
                Enrollment.objects.filter(
                    student=student,
                    group_id=int(group_id),
                    status__in=OPEN_ENROLLMENT_STATUSES,
                )
                .select_related("group")
                .first()
            )

        form = PaymentForm(request.POST)
        print_receipt = request.POST.get("action") == "print"
        entry = None
        if enrollment is None:
            messages.error(request, "O'quvchi tanlangan guruhga a'zo emas.")
        elif not form.is_valid():
            messages.error(request, "Forma ma'lumotlari noto'g'ri kiritildi.")
        else:
            try:
                entry = services.register_payment(
                    student=student,
                    group=enrollment.group,
                    amount=form.cleaned_data["amount"],
                    method=form.cleaned_data["method"],
                    entry_date=form.cleaned_data["entry_date"],
                    period=form.cleaned_data.get("period"),
                    comment=form.cleaned_data.get("comment", ""),
                    user=request.user,
                    idempotency_key=_idempotency_key(request),
                )
                messages.success(
                    request,
                    f"{student.full_name} uchun {form.cleaned_data['amount']:,} so'm to'lov qabul qilindi.",
                )
            except DuplicatePayment:
                messages.info(request, DUPLICATE_MESSAGE)
            except (ValidationError, PermissionDenied) as e:
                messages.error(request, str(e))

        next_url = request.POST.get("next", "")
        if not (next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()})):
            next_url = reverse("students:student_detail", args=[student.pk])
        if print_receipt and entry is not None:
            query = urlencode({"print": "1", "next": next_url})
            return redirect(f"{reverse('finance:receipt', args=[entry.pk])}?{query}")
        return redirect(next_url)


class RefundView(RoleRequiredMixin, View):
    """O'quvchiga pulni qaytarish (faqat direktor). Summa guruhdagi ortiqcha to'lovdan oshmaydi."""

    allowed_roles = [User.Role.DIRECTOR]
    template_name = "finance/refund.html"

    def _context(self, student, group, form):
        balance = services.get_balance(student, group)
        return {"student": student, "group": group, "form": form, "balance": balance, "refundable": max(balance, 0)}

    def _objects(self, student_id, group_id):
        student = get_object_or_404(Student, pk=student_id)
        group = get_object_or_404(Group, pk=group_id)
        return student, group

    def get(self, request, student_id, group_id):
        from django.utils import timezone

        student, group = self._objects(student_id, group_id)
        form = RefundForm(initial={"entry_date": timezone.localdate(), "method": "cash"})
        return render(request, self.template_name, self._context(student, group, form))

    def post(self, request, student_id, group_id):
        student, group = self._objects(student_id, group_id)
        form = RefundForm(request.POST)
        if form.is_valid():
            try:
                services.register_refund(
                    student=student,
                    group=group,
                    amount=form.cleaned_data["amount"],
                    method=form.cleaned_data["method"],
                    user=request.user,
                    entry_date=form.cleaned_data["entry_date"],
                    comment=form.cleaned_data["comment"],
                    idempotency_key=_idempotency_key(request),
                )
                messages.success(
                    request, f"{student.full_name}: {form.cleaned_data['amount']:,} so'm qaytarildi ({group.name})."
                )
                return redirect("students:student_detail", pk=student.pk)
            except DuplicatePayment:
                messages.info(request, DUPLICATE_MESSAGE)
                return redirect("students:student_detail", pk=student.pk)
            except (ValidationError, PermissionDenied) as exc:
                form.add_error(None, "; ".join(getattr(exc, "messages", [str(exc)])))
        return render(request, self.template_name, self._context(student, group, form))
