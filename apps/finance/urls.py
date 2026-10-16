"""Finance app URL patterns."""

from django.urls import path

from apps.finance import views

app_name = "finance"

urlpatterns = [
    # To'lovni ro'yxatga olish
    path(
        "payment/create/<int:student_id>/<int:group_id>/",
        views.PaymentCreateView.as_view(),
        name="payment_create",
    ),
    # Uzrli sabab: kreditlash (excused_credit)
    path(
        "excused-credit/<int:charge_id>/",
        views.ExcusedCreditView.as_view(),
        name="excused_credit",
    ),
    # Qarzdorlar ro'yxati
    path("debtors/", views.DebtorsListView.as_view(), name="debtors_list"),
    # Qarzdorlar CSV eksport
    path("debtors/export/", views.DebtorsExportView.as_view(), name="debtors_export"),
    # Kunlik kassa
    path("daily-cash/", views.DailyCashReportView.as_view(), name="daily_cash"),
    # Moliya dashboard
    path("dashboard/", views.FinanceDashboardView.as_view(), name="dashboard"),
    # Oylik hisob-kitob (ish haqi)
    path("payroll/", views.PayrollView.as_view(), name="payroll"),
    # Oylik hisob-kitob CSV eksport
    path("payroll/export/", views.PayrollExportView.as_view(), name="payroll_export"),
    # Oyni yopish
    path("payroll/close/", views.ClosePayrollMonthView.as_view(), name="payroll_close"),
    # Mening oyligim (teacher)
    path("my-salary/", views.MySalaryView.as_view(), name="my_salary"),
    # Bo'lim yaratish (+ BO'LIM)
    path("category/create/", views.CategoryCreateView.as_view(), name="category_create"),
    # Xarajat / Kirim kiritish
    path("expense/create/", views.ExpenseItemCreateView.as_view(), name="expense_create"),
    # Tezkor to'lov (+ TO'LOV)
    path("quick-payment/", views.QuickPaymentView.as_view(), name="quick_payment"),
    # Chek sozlamalari
    path("receipt/settings/", views.ReceiptSettingsView.as_view(), name="receipt_settings"),
    # To'lov cheki (Chop etish / Kvitansiya)
    path("receipt/<int:entry_id>/", views.PaymentReceiptView.as_view(), name="receipt"),
    # "To'lov" tugmasi: o'quvchi qidirish (kamida 3 harf), forma va saqlash
    path("payment/search/", views.PaymentStudentSearchView.as_view(), name="payment_search"),
    path(
        "payment/form/<int:student_id>/",
        views.PaymentModalFormView.as_view(),
        name="payment_modal_form",
    ),
    path("payment/add/", views.PaymentModalCreateView.as_view(), name="payment_add"),
    path("refund/<int:student_id>/<int:group_id>/", views.RefundView.as_view(), name="refund"),
]

