"""Guruh sahifasi uchun ma'lumotlar: oylik davomat jadvali ("o'quvchilar x dars sanalari"), dars kunlari, balanslar."""

from datetime import timedelta

from django.db.models import Sum

from apps.attendance.models import Attendance
from apps.finance.models import LedgerEntry
from apps.groups.models import Enrollment
from apps.schedule.models import Lesson, ScheduleSlot

WEEKDAYS = ["Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba", "Yakshanba"]
MONTHS = ["Yanvar", "Fevral", "Mart", "Aprel", "May", "Iyun", "Iyul", "Avgust", "Sentabr", "Oktabr", "Noyabr", "Dekabr"]

# Katakchada ko'rsatiladigan holatlar
STATE_LABEL = {
    "present": "Keldi",
    "late": "Kechikdi",
    "absent": "Kelmadi",
    "excused": "Sababli",
    "unmarked": "Belgilanmagan",
    "upcoming": "Dars hali o'tmagan",
    "cancelled": "Dars bekor qilingan",
    "na": "O'quvchi bu sanada guruhda emas",
}


def month_label(month_start):
    return f"{MONTHS[month_start.month - 1]} {month_start.year}"


def add_months(month_start, delta):
    index = month_start.year * 12 + (month_start.month - 1) + delta
    return month_start.replace(year=index // 12, month=index % 12 + 1, day=1)


def month_tabs(month_start, today, span=3):
    """Joriy oy atrofidagi oylar (har tomonga `span` ta)."""
    return [
        {
            "value": m.strftime("%Y-%m"),
            "label": month_label(m),
            "active": m == month_start,
            "is_today": m == today.replace(day=1),
        }
        for m in (add_months(month_start, d) for d in range(-span, span + 1))
    ]


def schedule_rows(group):
    """Guruhning haftalik dars kunlari: [{day, start, end, room}]"""
    slots = (
        ScheduleSlot.objects.filter(group=group, is_active=True)
        .select_related("room")
        .order_by("weekday", "start_time")
    )
    return [
        {"day": WEEKDAYS[s.weekday], "start": s.start_time, "end": s.end_time, "room": s.room.name} for s in slots
    ]


def student_balances(group):
    """{student_id: shu guruhdagi balans}."""
    rows = LedgerEntry.objects.filter(group=group).values("student").annotate(total=Sum("amount"))
    return {row["student"]: row["total"] for row in rows}


def attendance_grid(group, month_start, today):
    """Oy bo'yicha jadval: ustunlar — darslar, qatorlar — a'zolar. Har katakda holat va tahrirlash mumkinligi."""
    next_month = add_months(month_start, 1)
    lessons = list(
        Lesson.objects.filter(group=group, date__gte=month_start, date__lt=next_month).order_by("date", "start_time")
    )
    last_day = next_month - timedelta(days=1)
    enrollments = [
        e
        for e in group.enrollments.select_related("student").order_by("student__last_name", "student__first_name")
        if e.start_date <= last_day and (e.end_date is None or e.end_date >= month_start)
    ]
    marks = {
        (lesson_id, student_id): status
        for lesson_id, student_id, status in Attendance.objects.filter(lesson__in=lessons).values_list(
            "lesson_id", "student_id", "status"
        )
    }

    rows = []
    for e in enrollments:
        cells, present, absent = [], 0, 0
        for lesson in lessons:
            in_range = lesson.date >= e.start_date and (e.end_date is None or lesson.date <= e.end_date)
            if lesson.status == Lesson.Status.CANCELLED:
                state, editable = "cancelled", False
            elif not in_range:
                state, editable = "na", False
            elif (lesson.pk, e.student_id) in marks:
                state = marks[(lesson.pk, e.student_id)]
                editable = e.status in (Enrollment.Status.ACTIVE, Enrollment.Status.TRIAL) and lesson.date <= today
            elif lesson.date > today:
                state, editable = "upcoming", False
            else:
                state = "unmarked"
                editable = e.status in (Enrollment.Status.ACTIVE, Enrollment.Status.TRIAL)
            present += state in ("present", "late")
            absent += state == "absent"
            cells.append({"lesson": lesson, "state": state, "label": STATE_LABEL[state], "editable": editable})
        rows.append({"enrollment": e, "student": e.student, "cells": cells, "present": present, "absent": absent})
    return {"lessons": lessons, "rows": rows}
