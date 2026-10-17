"""
"Hisobotlar" sahifasi: barcha hisobotlar bir joyda. Har bir hisobot o'z sahifasining huquqlari bilan bir xil rollar uchun
ko'rinadi (direktor hammasini ko'radi): ro'yxat va sahifalarning huquqlari bir-biriga mos kelishi tests/test_reports.py da tekshiriladi.
"""

from dataclasses import dataclass, field

from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.permissions import user_has_role

R = User.Role


@dataclass(frozen=True)
class Report:
    key: str
    title: str
    description: str
    url_name: str
    roles: tuple
    icon: str  # kalit: templates/core/reports.html dagi belgi
    group: str  # "Moliya", "O'quvchilar", "Jarayon"
    query: str = ""
    extras: tuple = field(default_factory=tuple)  # (sarlavha, url_name, query)

    def url(self):
        return reverse(self.url_name) + self.query

    def extra_links(self):
        return [{"title": t, "url": reverse(n) + q} for t, n, q in self.extras]


REPORTS = (
    Report("finance", "Moliya paneli", "Tushum, chiqim, sof foyda, to'lov turlari bo'yicha taqsimot va oylik dinamika.",
           "finance:dashboard", (R.DIRECTOR,), "chart", "Moliya"),
    Report("cash", "Kunlik kassa", "Tanlangan kundagi to'lovlar: naqd, karta va boshqa usullar bo'yicha jami.",
           "finance:daily_cash", (R.ACCOUNTANT,), "cash", "Moliya"),
    Report("debtors", "Qarzdorlar", "Qarzi bor o'quvchilar, guruhlar va qarz summalari.",
           "finance:debtors_list", (R.ACCOUNTANT,), "warn", "Moliya",
           extras=(("Excel (CSV)", "finance:debtors_export", ""),)),
    Report("payroll", "Ish haqi hisob-kitobi", "O'qituvchilar oyligi: to'lovlardan foiz, qat'iy oylik va oyni yopish.",
           "finance:payroll", (R.DIRECTOR,), "wallet", "Moliya",
           extras=(("Yuklab olish (CSV)", "finance:payroll_export", ""),)),
    Report("students_xlsx", "O'quvchilar ro'yxati (Excel)", "Hozirgi filtr bo'yicha barcha o'quvchilarni Excel faylga yuklab olish.",
           "students:student_list", (R.ADMINISTRATOR, R.ACCOUNTANT), "table", "O'quvchilar", query="?export=xlsx"),
    Report("churn", "O'quvchilar ketishi", "Ketgan o'quvchilar va ketish sabablari bo'yicha hisobot.",
           "students:churn_report", (R.ADMINISTRATOR,), "exit", "O'quvchilar"),
    Report("attendance", "Guruhlar davomati", "Guruhni tanlab, oylik davomat jadvalini ko'rish va belgilash.",
           "groups:group_list", (R.ADMINISTRATOR, R.TEACHER), "check", "Jarayon"),
    Report("audit", "Harakatlar jurnali", "Kim, qachon va nimani o'zgartirgani: foydalanuvchilar, to'lovlar, guruhlar.",
           "core:audit_log", (R.DIRECTOR,), "doc", "Jarayon"),
)


def reports_for(user):
    """Foydalanuvchi ko'ra oladigan hisobotlar, bo'limlar bo'yicha: [(bo'lim, [Report, ...]), ...]."""
    visible = [r for r in REPORTS if user_has_role(user, r.roles)]
    groups = {}
    for report in visible:
        groups.setdefault(report.group, []).append(report)
    return list(groups.items())
