"""
Qo'ng'iroqcha va bosh sahifadagi "Eslatmalar": hozirgi holatdan hisoblanadi (alohida jadval yo'q).

Rollar:
- direktor: qarzdorlar, davomati belgilanmagan darslar, bugungi sinov darslari, guruhsiz o'quvchilar, tug'ilgan kunlar;
- administrator: qarzdan tashqari hammasi (moliya ko'rsatkichlari faqat direktorda);
- buxgalter: qarzdorlar;
- o'qituvchi: faqat o'z darslari va o'quvchilari bo'yicha (davomat, sinov, tug'ilgan kun).
"""

from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Sum
from django.urls import reverse
from django.utils import timezone

from apps.finance.models import LedgerEntry
from apps.groups.models import Enrollment
from apps.schedule.models import Lesson
from apps.students.models import Student

OPEN = [Enrollment.Status.ACTIVE, Enrollment.Status.TRIAL, Enrollment.Status.FROZEN]


def _alert(key, text, url, tone):
    return {"key": key, "text": text, "url": url, "tone": tone}


def build_alerts(user, now=None):
    now = timezone.localtime(now) if now else timezone.localtime()
    today = now.date()
    role = getattr(user, "role", "")
    teacher = None
    if role == "teacher":
        try:
            teacher = user.teacher_profile
        except ObjectDoesNotExist:
            return []
    items = []

    if role in ("director", "accountant"):
        debtors = (
            LedgerEntry.objects.values("student", "group")
            .annotate(balance=Sum("amount"))
            .filter(balance__lt=0)
            .values_list("student", flat=True)
        )
        count = len(set(debtors))
        if count:
            items.append(_alert("debt", f"{count} ta o'quvchida qarz bor", reverse("finance:debtors_list"), "red"))

    if role in ("director", "administrator", "teacher"):
        lessons = Lesson.objects.filter(date=today).exclude(status=Lesson.Status.CANCELLED)
        if teacher is not None:
            lessons = lessons.filter(teacher=teacher)
        unmarked = list(
            lessons.filter(status=Lesson.Status.SCHEDULED, end_time__lt=now.time())
            .order_by("start_time")
            .values_list("id", flat=True)
        )
        if unmarked:
            url = (
                reverse("schedule:lesson_detail", args=[unmarked[0]])
                if len(unmarked) == 1
                else reverse("schedule:weekly_schedule")
            )
            items.append(_alert("unmarked", f"{len(unmarked)} ta darsda davomat belgilanmagan", url, "red"))

        # Bitta filter() ichida: sana va holat aynan bugungi darsga tegishli bo'lsin
        trials = Enrollment.objects.filter(
            status=Enrollment.Status.TRIAL,
            group__lessons__date=today,
            group__lessons__status__in=[Lesson.Status.SCHEDULED, Lesson.Status.HELD],
        )
        if teacher is not None:
            trials = trials.filter(group__teacher=teacher)
        trial_count = trials.distinct().count()
        if trial_count:
            items.append(_alert("trial", f"Bugun {trial_count} ta sinov darsi", reverse("students:student_list") + "?status=trial", "violet"))

        birthdays = Student.objects.filter(
            status=Student.Status.ACTIVE, birth_date__month=today.month, birth_date__day=today.day
        )
        if teacher is not None:
            birthdays = birthdays.filter(enrollments__group__teacher=teacher, enrollments__status__in=OPEN).distinct()
        ids = list(birthdays.values_list("id", flat=True)[:50])
        if ids:
            url = reverse("students:student_detail", args=[ids[0]]) if len(ids) == 1 else reverse("students:student_list")
            items.append(_alert("birthday", f"Bugun {len(ids)} ta o'quvchining tug'ilgan kuni", url, "gold"))

    if role in ("director", "administrator"):
        no_group = Student.objects.filter(status=Student.Status.ACTIVE).exclude(enrollments__status__in=OPEN).count()
        if no_group:
            items.append(_alert("no_group", f"{no_group} ta o'quvchi guruhga qo'shilmagan", reverse("students:student_list") + "?nogroup=1", "teal"))

    return items
