"""
Bosh sahifa: ko'rsatkichlarning o'tgan oyga nisbatan o'zgarishi (kartalardagi strelkalar).

Har bir o'zgarish bir xil formula bilan ikki sana uchun hisoblanadi (bugun va o'tgan oyning shu kuni),
shuning uchun farq aniq bo'ladi:
- o'quvchilar, o'rinlar, sinov, muzlatilgan: a'zolik holati EnrollmentLog tarixidan tiklanadi;
- guruhlar: boshlanish va tugash sanasi bo'yicha;
- qarz: shu sanagacha bo'lgan yozuvlar bo'yicha (student + guruh balansi);
- tushum, yangi qabul, ketganlar: oy boshidan bugungacha va o'tgan oyning xuddi shu davri;
- bugungi darslar: o'tgan haftaning shu kuni bilan.
O'qituvchilar soni uchun tarix saqlanmaydi, shuning uchun strelka ko'rsatilmaydi.
"""

from collections import defaultdict
from datetime import date, timedelta

from django.db.models import Count, Q, Sum
from django.utils import timezone

from apps.finance.models import LedgerEntry
from apps.groups.models import Enrollment, EnrollmentLog, Group
from apps.schedule.models import Lesson

OPEN = {Enrollment.Status.ACTIVE, Enrollment.Status.TRIAL, Enrollment.Status.FROZEN}
MONTH = "o'tgan oyga nisbatan"
WEEK = "o'tgan haftaga nisbatan"


def same_day_last_month(day: date) -> date:
    prev_last = day.replace(day=1) - timedelta(days=1)
    return prev_last.replace(day=min(day.day, prev_last.day))


def _status_on(enrollment, logs, day):
    """A'zolikning berilgan kundagi holati (None: hali boshlanmagan)."""
    if enrollment["start_date"] > day:
        return None
    before = after = None
    for log in logs:
        if timezone.localdate(log["changed_at"]) <= day:
            before = log
        else:
            after = log
            break
    if before is not None:
        return before["new_status"]
    if after is not None:
        # Keyin yozilgan birinchi o'zgarishdan oldingi holat (yaratilish yozuvida eski holat bo'sh)
        return after["old_status"] or after["new_status"]
    return enrollment["status"]


def _group_active_on(group, day):
    if group["start_date"] > day:
        return False
    if group["end_date"] is not None:
        return group["end_date"] >= day
    # Yopilgan, lekin tugash sanasi yozilmagan guruh: qachon yopilgani noma'lum, hisobga olinmaydi
    return group["status"] == Group.Status.ACTIVE


def _enrollment_counts(days):
    """Har bir kun uchun: ochiq o'quvchilar, faol guruhlardagi o'rinlar, sinov, muzlatilgan o'quvchilar, guruhlar."""
    enrollments = list(Enrollment.objects.values("id", "student_id", "group_id", "start_date", "status"))
    logs = defaultdict(list)
    for log in EnrollmentLog.objects.order_by("changed_at", "id").values("enrollment_id", "old_status", "new_status", "changed_at"):
        logs[log["enrollment_id"]].append(log)
    groups = list(Group.objects.values("id", "start_date", "end_date", "status"))

    result = {}
    for day in days:
        students, frozen = set(), set()
        seats = trial = 0
        active_groups = {g["id"] for g in groups if _group_active_on(g, day)}
        for e in enrollments:
            status = _status_on(e, logs[e["id"]], day)
            if status not in OPEN:
                continue
            students.add(e["student_id"])
            if e["group_id"] in active_groups:
                seats += 1
            if status == Enrollment.Status.TRIAL:
                trial += 1
            elif status == Enrollment.Status.FROZEN:
                frozen.add(e["student_id"])
        result[day] = {
            "active_students": len(students),
            "total_enrollments": seats,
            "trial_count": trial,
            "frozen_count": len(frozen),
            "active_groups": len(active_groups),
        }
    return result


def _debt_on(day):
    balances = list(
        LedgerEntry.objects.filter(entry_date__lte=day)
        .values("student", "group")
        .annotate(balance=Sum("amount"))
        .filter(balance__lt=0)
        .values_list("balance", flat=True)
    )
    return len(balances), -sum(balances)


def _trend(now, before, *, higher_is_good=True, caption=MONTH, money=False, percent=False):
    delta = now - before
    item = {"delta": delta, "abs": abs(delta), "caption": caption, "money": money}
    if delta == 0:
        item["state"] = "flat"
    else:
        item["state"] = "good" if (delta > 0) == higher_is_good else "bad"
    if percent and before > 0:
        item["percent"] = round(abs(delta) * 100 / before)
    return item


def dashboard_trends(user=None, today=None) -> dict:
    """Bosh sahifa kartalari uchun o'zgarishlar: {ko'rsatkich: {delta, abs, state, caption, ...}}."""
    today = today or timezone.localdate()
    ref = same_day_last_month(today)
    month_start = today.replace(day=1)
    prev_start = ref.replace(day=1)
    can_see_money = getattr(user, "role", None) != "administrator"

    counts = _enrollment_counts([today, ref])
    now, then = counts[today], counts[ref]
    trends = {
        "active_students": _trend(now["active_students"], then["active_students"]),
        "active_groups": _trend(now["active_groups"], then["active_groups"]),
        "total_enrollments": _trend(now["total_enrollments"], then["total_enrollments"]),
        "trial_count": _trend(now["trial_count"], then["trial_count"]),
        "frozen_count": _trend(now["frozen_count"], then["frozen_count"], higher_is_good=False),
    }

    flows = Enrollment.objects.aggregate(
        new_now=Count("id", filter=Q(start_date__gte=month_start, start_date__lte=today)),
        new_then=Count("id", filter=Q(start_date__gte=prev_start, start_date__lte=ref)),
        left_now=Count("id", filter=Q(status=Enrollment.Status.FINISHED, end_date__gte=month_start, end_date__lte=today)),
        left_then=Count("id", filter=Q(status=Enrollment.Status.FINISHED, end_date__gte=prev_start, end_date__lte=ref)),
    )
    trends["new_enrollments"] = _trend(flows["new_now"], flows["new_then"])
    trends["left_students"] = _trend(flows["left_now"], flows["left_then"], higher_is_good=False)

    week_ago = today - timedelta(days=7)
    lessons = Lesson.objects.exclude(status=Lesson.Status.CANCELLED).aggregate(
        now=Count("id", filter=Q(date=today)), then=Count("id", filter=Q(date=week_ago))
    )
    trends["today_lessons"] = _trend(lessons["now"], lessons["then"], caption=WEEK)

    if can_see_money:
        debtors_now, debt_now = _debt_on(today)
        debtors_then, debt_then = _debt_on(ref)
        trends["debtors_count"] = _trend(debtors_now, debtors_then, higher_is_good=False)
        trends["total_debt"] = _trend(debt_now, debt_then, higher_is_good=False, money=True)
        income = LedgerEntry.objects.filter(kind__in=[LedgerEntry.Kind.PAYMENT, LedgerEntry.Kind.REFUND]).aggregate(
            now=Sum("amount", filter=Q(entry_date__gte=month_start, entry_date__lte=today)),
            then=Sum("amount", filter=Q(entry_date__gte=prev_start, entry_date__lte=ref)),
        )
        trends["monthly_income"] = _trend(income["now"] or 0, income["then"] or 0, money=True, percent=True)
    return trends
