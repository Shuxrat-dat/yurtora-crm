"""Business logic services for schedule: lesson generation, cancellation, rescheduling."""

from datetime import date, timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.schedule.models import Lesson, ScheduleSlot


def _check_conflicts(
    room_id,
    teacher_id,
    lesson_date: date,
    start_time,
    end_time,
    exclude_lesson_pk=None,
):
    """
    Xona va o'qituvchi uchun bir kunda vaqt to'qnashuvi (conflict) tekshiruvi.
    To'qnashuv aniqlansa ValidationError ko'tariladi.
    """
    qs = Lesson.objects.filter(
        date=lesson_date,
        status__in=[Lesson.Status.SCHEDULED, Lesson.Status.HELD],
        start_time__lt=end_time,
        end_time__gt=start_time,
    )
    if exclude_lesson_pk:
        qs = qs.exclude(pk=exclude_lesson_pk)

    # Xona to'qnashuvi
    room_conflict = qs.filter(room_id=room_id).first()
    if room_conflict:
        raise ValidationError(
            f"Xona to'qnashuvi: {room_conflict.room.name} xonasi "
            f"{lesson_date} kuni {room_conflict.start_time:%H:%M}–{room_conflict.end_time:%H:%M} "
            f"da allaqachon band ({room_conflict.group.name})."
        )

    # O'qituvchi to'qnashuvi
    teacher_conflict = qs.filter(teacher_id=teacher_id).first()
    if teacher_conflict:
        raise ValidationError(
            f"O'qituvchi to'qnashuvi: {teacher_conflict.teacher.full_name} "
            f"{lesson_date} kuni {teacher_conflict.start_time:%H:%M}–{teacher_conflict.end_time:%H:%M} "
            f"da allaqachon band ({teacher_conflict.group.name})."
        )


@transaction.atomic
def generate_lessons(group, date_from: date, date_to: date) -> list[Lesson]:
    """
    Guruhning faol jadval slotlari asosida berilgan davr uchun darslar yaratadi.
    Takroriy chaqiruv dublikat yaratmaydi (mavjud bo'lsa o'tkazib yuboradi).
    Har bir yangi dars uchun konflikt tekshiruvi o'tkaziladi.
    """
    if date_to < date_from:
        raise ValidationError("Tugash sanasi boshlanish sanasidan kichik bo'lmasligi kerak.")

    slots = ScheduleSlot.objects.filter(
        group=group, is_active=True
    ).select_related("room", "group__teacher")

    if not slots.exists():
        raise ValidationError(
            f"'{group.name}' guruhining faol jadval slotlari topilmadi."
        )

    created = []
    current = date_from
    while current <= date_to:
        day_of_week = current.weekday()  # Python: Monday=0, Sunday=6
        day_slots = [s for s in slots if s.weekday == day_of_week]

        for slot in day_slots:
            teacher = group.teacher  # guruhning asosiy o'qituvchisi

            # Agar bu dars mavjud bo'lsa — o'tkazib yubor
            exists = Lesson.objects.filter(
                group=group,
                date=current,
                start_time=slot.start_time,
            ).exists()
            if exists:
                current = current + timedelta(days=0)  # continue below
                continue

            # Konflikt tekshiruvi
            _check_conflicts(
                room_id=slot.room_id,
                teacher_id=teacher.pk,
                lesson_date=current,
                start_time=slot.start_time,
                end_time=slot.end_time,
            )

            lesson = Lesson.objects.create(
                group=group,
                date=current,
                start_time=slot.start_time,
                end_time=slot.end_time,
                room=slot.room,
                teacher=teacher,
                status=Lesson.Status.SCHEDULED,
            )
            created.append(lesson)

        current += timedelta(days=1)

    return created


@transaction.atomic
def cancel_lesson(lesson: Lesson, reason: str = "", user=None) -> Lesson:
    """
    Darsni bekor qiladi. Sabab note ga yoziladi. Dars uchun hisoblangan to'lovlar qaytariladi: o'tilgan dars
    va orqaga qarab qo'shilgan o'quvchiga jadval bo'yicha hisoblangan (hali belgilanmagan) dars uchun ham.
    """
    if lesson.status == Lesson.Status.CANCELLED:
        raise ValidationError("Dars allaqachon bekor qilingan.")

    note_prefix = f"[Bekor qilindi] {reason}".strip()
    lesson.note = (note_prefix + "\n" + lesson.note).strip() if lesson.note else note_prefix
    lesson.status = Lesson.Status.CANCELLED
    lesson.save(update_fields=["status", "note", "updated_at"])
    from apps.finance.services import reverse_lesson_charges

    reverse_lesson_charges(lesson, user=user)
    return lesson


@transaction.atomic
def reschedule_lesson(
    lesson: Lesson,
    new_date: date,
    new_start_time,
    new_end_time,
    new_room=None,
    reason: str = "",
) -> Lesson:
    """Darsni boshqa sana/vaqtga ko'chiradi. Sabab note ga yoziladi. Konflikt tekshiriladi."""
    if lesson.status == Lesson.Status.CANCELLED:
        raise ValidationError("Bekor qilingan darsni ko'chirish mumkin emas.")

    room = new_room or lesson.room

    _check_conflicts(
        room_id=room.pk,
        teacher_id=lesson.teacher_id,
        lesson_date=new_date,
        start_time=new_start_time,
        end_time=new_end_time,
        exclude_lesson_pk=lesson.pk,
    )

    old_info = f"{lesson.date} {lesson.start_time:%H:%M}"
    note_prefix = f"[Ko'chirildi: {old_info} dan] {reason}".strip()
    lesson.note = (note_prefix + "\n" + lesson.note).strip() if lesson.note else note_prefix
    lesson.date = new_date
    lesson.start_time = new_start_time
    lesson.end_time = new_end_time
    lesson.room = room
    lesson.save(update_fields=["date", "start_time", "end_time", "room", "note", "updated_at"])
    return lesson



@transaction.atomic
def mark_lesson_held(lesson: Lesson, user) -> Lesson:
    """
    Darsni o'tkazilgan (held) deb belgilaydi va to'lovlarni hisoblaydi.

    Qoidalar:
    - Bekor qilingan (cancelled) darsni held qilish mumkin emas.
    - Kelajakdagi sana uchun held belgilash mumkin emas.
    - Huquqlar: administrator, director, teacher (faqat o'z guruhlari uchun).
    - Dars held qilingandan keyin accrue_lesson_charges avtomatik chaqiriladi.
    """
    if lesson.status == Lesson.Status.CANCELLED:
        raise ValidationError("Bekor qilingan darsni o'tkazilgan deb belgilab bo'lmaydi.")
    if lesson.status == Lesson.Status.HELD:
        raise ValidationError("Dars allaqachon o'tilgan deb belgilangan.")

    # Kelajakdagi sana tekshiruvi (Toshkent sanasi bo'yicha)
    today = timezone.localdate()
    if lesson.date > today:
        raise ValidationError(
            f"Kelajakdagi dars ({lesson.date}) uchun 'held' belgilash mumkin emas."
        )

    # Huquqlar tekshiruvi
    if user is not None:
        is_admin_or_director = (
            getattr(user, "is_director", False)
            or getattr(user, "is_administrator", False)
            or getattr(user, "is_superuser", False)
        )
        is_teacher = getattr(user, "is_teacher", False) or (
            getattr(user, "role", "") == "teacher"
        )

        if is_teacher:
            # O'qituvchi faqat o'z guruhidagi darslarni held qila oladi
            lesson_teacher_user = (
                lesson.group.teacher.user if lesson.group and lesson.group.teacher else None
            )
            if lesson_teacher_user != user:
                raise PermissionDenied(
                    "O'qituvchi faqat o'z guruhidagi darslarni o'tkazilgan deb belgilashi mumkin."
                )
        elif not is_admin_or_director:
            raise PermissionDenied("Ushbu amalni bajarish uchun ruxsat yo'q.")

    # Statusni o'zgartirish
    lesson.status = Lesson.Status.HELD
    lesson.save(update_fields=["status", "updated_at"])

    # To'lovlarni hisoblash: xato bo'lsa butun amal bekor qilinadi (dars "o'tildi" bo'lib, hisobsiz qolmaydi)
    from apps.finance.services import accrue_lesson_charges

    accrue_lesson_charges(lesson)

    return lesson


def reassign_future_lessons(group, teacher, today=None):
    """Guruh o'qituvchisi almashganda bugundan keyingi rejalashtirilgan darslar yangi o'qituvchiga o'tkaziladi.

    O'tilgan va bekor qilingan darslar o'zgarmaydi. (yangilangan, to'qnashuvlar) soni qaytariladi:
    yangi o'qituvchi shu vaqtda boshqa guruhda band bo'lsa ham dars o'tkaziladi, lekin to'qnashuv haqida xabar beriladi.
    """
    today = today or timezone.localdate()
    lessons = list(Lesson.objects.filter(group=group, status=Lesson.Status.SCHEDULED, date__gte=today))
    conflicts = 0
    for lesson in lessons:
        clash = Lesson.objects.filter(
            teacher=teacher,
            date=lesson.date,
            status__in=[Lesson.Status.SCHEDULED, Lesson.Status.HELD],
            start_time__lt=lesson.end_time,
            end_time__gt=lesson.start_time,
        ).exclude(pk=lesson.pk)
        conflicts += clash.exists()
    Lesson.objects.filter(pk__in=[lesson.pk for lesson in lessons]).update(teacher=teacher)
    return len(lessons), conflicts


SHORT_WEEKDAYS = ["Du", "Se", "Chor", "Pay", "Ju", "Sha", "Yak"]


def group_schedule_summaries(group_ids):
    """{group_id: "Du, Chor, Ju 09:00–11:00"} — guruhning faol slotlari qisqacha."""
    from collections import defaultdict

    from apps.schedule.models import ScheduleSlot

    by_group = defaultdict(list)
    slots = ScheduleSlot.objects.filter(group_id__in=group_ids, is_active=True).order_by(
        "weekday", "start_time"
    )
    for slot in slots:
        by_group[slot.group_id].append(slot)
    result = {}
    for group_id, items in by_group.items():
        days = ", ".join(SHORT_WEEKDAYS[slot.weekday] for slot in items)
        first = items[0]
        result[group_id] = f"{days} {first.start_time:%H:%M}–{first.end_time:%H:%M}"
    return result
