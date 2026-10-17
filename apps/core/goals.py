"""Maqsadlar: davr chegaralari va progressni hisoblash (bosh sahifa va "Maqsadlar" sahifasi)."""

from datetime import timedelta

from django.db.models import Sum
from django.utils import timezone

from apps.core.models import Goal
from apps.finance.models import LedgerEntry
from apps.students.models import Student


def period_range(period, today=None):
    today = today or timezone.localdate()
    if period == Goal.Period.WEEK:
        return today - timedelta(days=today.weekday()), today
    if period == Goal.Period.MONTH:
        return today.replace(day=1), today
    return today, today


def goal_progress(goal, today=None):
    start, end = period_range(goal.period, today)
    if goal.metric == Goal.Metric.INCOME:
        current = (
            LedgerEntry.objects.filter(
                kind__in=[LedgerEntry.Kind.PAYMENT, LedgerEntry.Kind.REFUND], entry_date__gte=start, entry_date__lte=end
            ).aggregate(total=Sum("amount"))["total"]
            or 0
        )
    else:
        current = Student.objects.filter(created_at__date__gte=start, created_at__date__lte=end).count()
    current = max(current, 0)
    percent = min(100, int(current * 100 / goal.target)) if goal.target else 0
    return {
        "goal": goal,
        "current": current,
        "target": goal.target,
        "percent": percent,
        "done": current >= goal.target,
        "money": goal.metric == Goal.Metric.INCOME,
        "start": start,
        "end": end,
    }


def main_goal_for(user, today=None):
    """Bosh sahifa uchun asosiy maqsad progressi. Tushum maqsadi faqat direktorga ko'rinadi."""
    goal = Goal.objects.filter(is_main=True).first()
    if goal is None:
        return None
    if goal.metric == Goal.Metric.INCOME and getattr(user, "role", "") != "director":
        return None
    return goal_progress(goal, today)
