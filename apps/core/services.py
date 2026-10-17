"""Dashboard metrics calculation services."""

from datetime import date, time
from typing import Any

from django.db.models import Sum

from apps.finance.models import LedgerEntry
from apps.finance.services import get_debtors
from apps.groups.models import Enrollment, Group
from apps.schedule.models import Lesson, Room, ScheduleSlot
from apps.students.models import Student
from apps.teachers.models import Teacher


def get_dashboard_metrics(user=None) -> dict[str, Any]:
    """
    Calculate dashboard metrics for admin/director.
    Returns all key metrics for the home page cards.
    """
    today = date.today()
    current_month_start = today.replace(day=1)

    is_admin = user and getattr(user, "role", None) == "administrator"

    metrics = {
        # Active groups
        "active_groups": Group.objects.filter(status=Group.Status.ACTIVE).count(),
        # Active students with at least one open enrollment
        "active_students": Student.objects.filter(
            status=Student.Status.ACTIVE,
            enrollments__status__in=[
                Enrollment.Status.TRIAL,
                Enrollment.Status.ACTIVE,
                Enrollment.Status.FROZEN,
            ],
        )
        .distinct()
        .count(),
        # Total enrollments in active groups (trial/active/frozen)
        "total_enrollments": Enrollment.objects.filter(
            group__status=Group.Status.ACTIVE,
            status__in=[
                Enrollment.Status.TRIAL,
                Enrollment.Status.ACTIVE,
                Enrollment.Status.FROZEN,
            ],
        ).count(),
        # Students with negative balance
        "debtors_count": None if is_admin else len(get_debtors()),
        # Total debt amount
        "total_debt": None if is_admin else sum(
            abs(debtor["balance"]) for debtor in get_debtors() if debtor["balance"] < 0
        ),
        # Trial enrollments
        "trial_count": Enrollment.objects.filter(
            status=Enrollment.Status.TRIAL
        ).count(),
        # Students with status "Muzlatilgan" (frozen)
        "frozen_count": Student.objects.filter(
            status=Student.Status.FROZEN
        ).count(),
        # Students who left this month (finished enrollments)
        "left_students": Enrollment.objects.filter(
            status=Enrollment.Status.FINISHED,
            end_date__year=today.year,
            end_date__month=today.month,
        ).count(),
        # New enrollments this month
        "new_enrollments": Enrollment.objects.filter(
            start_date__gte=current_month_start, start_date__lte=today
        ).count(),
        # Active teachers
        "active_teachers": Teacher.objects.filter(is_active=True).count(),
        # Today's lessons (not cancelled)
        "today_lessons": Lesson.objects.filter(
            date=today,
        )
        .exclude(status=Lesson.Status.CANCELLED)
        .count(),
        # Monthly income (payments in current month)
        "monthly_income": None if is_admin else (
            LedgerEntry.objects.filter(
                kind__in=[LedgerEntry.Kind.PAYMENT, LedgerEntry.Kind.REFUND],  # sof tushum
                entry_date__gte=current_month_start,
                entry_date__lte=today,
            ).aggregate(total=Sum("amount"))["total"]
            or 0
        ),
    }

    return metrics


def get_teacher_today_lessons(teacher):
    """Get today's lessons for a specific teacher."""
    today = date.today()
    return (
        Lesson.objects.filter(
            teacher=teacher,
            date=today,
        )
        .exclude(status=Lesson.Status.CANCELLED)
        .select_related("group", "room", "group__course")
        .order_by("start_time")
    )


# Room occupancy calculation constants
WORK_DAY_START = time(8, 0)  # 08:00
WORK_DAY_END = time(21, 0)  # 21:00
WORK_DAY_MINUTES = 13 * 60  # 08:00 to 21:00 = 13 hours = 780 minutes


def compute_room_occupancy() -> int:
    """
    Calculate room occupancy percentage for today.
    Formula: (total lesson minutes today) / (active rooms * work day minutes) * 100
    """
    today = date.today()
    weekday = today.weekday()  # Monday=0, Sunday=6

    # Count active rooms
    active_rooms_count = Room.objects.filter(is_active=True).count()

    if active_rooms_count == 0:
        return 0

    # Get all schedule slots for today's weekday
    slots = ScheduleSlot.objects.filter(weekday=weekday, is_active=True)

    # Calculate total minutes of scheduled lessons
    total_minutes = 0
    for slot in slots:
        # Calculate duration in minutes
        start_dt = slot.start_time
        end_dt = slot.end_time

        duration_minutes = (
            end_dt.hour * 60 + end_dt.minute - (start_dt.hour * 60 + start_dt.minute)
        )
        total_minutes += duration_minutes

    # Calculate occupancy percentage
    total_available_minutes = active_rooms_count * WORK_DAY_MINUTES
    if total_available_minutes == 0:
        return 0

    occupancy = (total_minutes * 100) // total_available_minutes
    return min(occupancy, 100)  # Cap at 100%


def get_schedule_grid_data(
    weekday: int, step_minutes: int = 30, teacher=None
) -> dict[str, Any]:
    """
    Get schedule grid data for a specific weekday.

    Args:
        weekday: 0=Monday, 6=Sunday
        step_minutes: time step (15, 30, or 60 minutes)
        teacher: optional teacher filter (for teacher role)

    Returns:
        dict with rooms, time_slots, and grid_matrix
    """
    # Get rooms
    if teacher:
        # Teacher sees only their groups' rooms
        rooms = (
            Room.objects.filter(
                schedule_slots__group__teacher=teacher,
                schedule_slots__weekday=weekday,
                schedule_slots__is_active=True,
            )
            .distinct()
            .order_by("name")
        )
    else:
        rooms = Room.objects.filter(is_active=True).order_by("name")

    rooms = list(rooms)

    # Generate time slots from 08:00 to 21:00
    time_slots = []
    current_time = WORK_DAY_START
    while current_time < WORK_DAY_END:
        time_slots.append(current_time)
        # Add step_minutes
        minutes = current_time.hour * 60 + current_time.minute + step_minutes
        current_time = time(minutes // 60, minutes % 60)

    # Get schedule slots for this weekday with related data
    slots_query = (
        ScheduleSlot.objects.filter(weekday=weekday, is_active=True)
        .select_related("group", "group__course", "group__teacher", "room")
        .order_by("room__name", "start_time")
    )

    if teacher:
        slots_query = slots_query.filter(group__teacher=teacher)

    slots = list(slots_query)

    # Build grid matrix: room x time_slot
    # Each cell: None (empty) or dict with slot info
    grid_matrix = []

    for room in rooms:
        room_slots = [slot for slot in slots if slot.room_id == room.id]

        # Check for conflicts in this room
        conflicts = set()
        for i, slot1 in enumerate(room_slots):
            for slot2 in room_slots[i + 1 :]:
                # Check if time overlaps
                if (
                    slot1.start_time < slot2.end_time
                    and slot1.end_time > slot2.start_time
                ):
                    conflicts.add(slot1.id)
                    conflicts.add(slot2.id)

        # Build row for this room
        row = []
        occupied_until = -1  # Track colspan occupation

        for col_idx, ts in enumerate(time_slots):
            if col_idx < occupied_until:
                # This cell is part of previous colspan
                continue

            # Find slot starting at this time
            cell = None
            for slot in room_slots:
                if slot.start_time == ts:
                    # Calculate colspan
                    duration_minutes = (
                        slot.end_time.hour * 60
                        + slot.end_time.minute
                        - (slot.start_time.hour * 60 + slot.start_time.minute)
                    )
                    colspan = max(1, duration_minutes // step_minutes)
                    occupied_until = col_idx + colspan

                    cell = {
                        "slot": slot,
                        "colspan": colspan,
                        "has_conflict": slot.id in conflicts,
                    }
                    break

            row.append(cell)

        grid_matrix.append({"room": room, "cells": row})

    return {
        "time_slots": time_slots,
        "grid_matrix": grid_matrix,
        "weekday": weekday,
        "step_minutes": step_minutes,
        "timeline": build_room_timeline(rooms, slots, step_minutes),
    }


def _minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def build_room_timeline(rooms, slots, step_minutes: int = 30) -> dict[str, Any]:
    """
    Xonalar bandligi lentasi: har bir dars xona qatorida vaqtiga mos joy va uzunlikda (foizlarda).
    Ish kuni 08:00–21:00, undan tashqaridagi darslar bo'lsa kun soat chegarasigacha kengaytiriladi.
    Boshlanishi qadamga to'g'ri kelmaydigan darslar ham ko'rinadi (masalan, 14:15).
    """
    start = _minutes(WORK_DAY_START)
    end = _minutes(WORK_DAY_END)
    for slot in slots:
        start = min(start, _minutes(slot.start_time) // 60 * 60)
        end = max(end, -(-_minutes(slot.end_time) // 60) * 60)
    span = max(end - start, 60)

    # Guruhdagi o'rinlar: faol + sinov + muzlatilgan a'zolar (bitta so'rov)
    enrolled = {}
    group_ids = {slot.group_id for slot in slots}
    if group_ids:
        from django.db.models import Count

        enrolled = dict(
            Enrollment.objects.filter(
                group_id__in=group_ids,
                status__in=[Enrollment.Status.ACTIVE, Enrollment.Status.TRIAL, Enrollment.Status.FROZEN],
            )
            .values_list("group_id")
            .annotate(n=Count("id"))
        )

    rows = []
    for room in rooms:
        room_slots = sorted((s for s in slots if s.room_id == room.id), key=lambda s: s.start_time)
        blocks = []
        for slot in room_slots:
            s, e = _minutes(slot.start_time), _minutes(slot.end_time)
            conflict = any(
                o.id != slot.id and o.start_time < slot.end_time and o.end_time > slot.start_time
                for o in room_slots
            )
            blocks.append(
                {
                    "slot": slot,
                    "left": (s - start) * 100 / span,
                    "width": max(e - s, 15) * 100 / span,
                    "has_conflict": conflict,
                    "enrolled": enrolled.get(slot.group_id, 0),
                    "capacity": slot.group.max_students,
                }
            )
        rows.append({"room": room, "blocks": blocks})

    labels = []
    minute = start
    while minute <= end:
        labels.append({"time": time(minute // 60, minute % 60) if minute < 24 * 60 else time(23, 59), "left": (minute - start) * 100 / span})
        minute += step_minutes
    return {"rows": rows, "labels": labels, "columns": span // step_minutes}
