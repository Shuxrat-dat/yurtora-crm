"""Obuna: holat (sinov, faol, imtiyozli, faqat ko'rish, bloklangan), to'lovni qayd etish va tarif limitlari."""

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.utils import timezone

TRIAL_DAYS = 14


def stage_days():
    """(imtiyozli kunlar, faqat ko'rish kunlari) — egasi panelidagi sozlama. Ikkalasi 0 bo'lsa (standart),
    muddat tugagan kunning ertasidan markaz o'chadi va tushuntirish oynasi chiqadi."""
    from .models import PlatformSettings

    settings_obj = PlatformSettings.get()
    return settings_obj.grace_days, settings_obj.read_only_days

TRIAL, ACTIVE, GRACE, READ_ONLY, BLOCKED = "trial", "active", "grace", "read_only", "blocked"
STATUS_LABELS = {
    TRIAL: "Sinov davri",
    ACTIVE: "Faol",
    GRACE: "Muddati tugagan (imtiyozli kunlar)",
    READ_ONLY: "Faqat ko'rish rejimi",
    BLOCKED: "Bloklangan",
}


@dataclass(frozen=True)
class Subscription:
    status: str
    access_until: date | None  # shu kungacha to'liq ruxsat (sinov yoki to'lov); None — muddat belgilanmagan
    days_left: int | None      # access_until gacha qolgan kunlar (manfiy — o'tib ketgan)
    read_only_from: date | None
    blocked_from: date | None
    manual_block: bool = False

    @property
    def label(self) -> str:
        return STATUS_LABELS[self.status]

    @property
    def full_access(self) -> bool:
        return self.status in (TRIAL, ACTIVE, GRACE)


def subscription(center, today=None) -> Subscription:
    """Markaz obunasi holati. Muddatlar belgilanmagan markaz (Yurtora ichki/test) — cheklovsiz faol."""
    today = today or timezone.localdate()
    if not center.is_active:
        return Subscription(BLOCKED, None, None, None, None, manual_block=True)
    dates = [d for d in (center.trial_ends_at, center.paid_until) if d]
    if not dates:
        return Subscription(ACTIVE, None, None, None, None)
    access_until = max(dates)
    grace_days, read_only_days = stage_days()
    read_only_from = access_until + timedelta(days=grace_days + 1)
    blocked_from = read_only_from + timedelta(days=read_only_days)
    days_left = (access_until - today).days
    if today <= access_until:
        paid = center.paid_until is not None and center.paid_until >= today
        status = ACTIVE if paid else TRIAL
    elif today < read_only_from:
        status = GRACE
    elif today < blocked_from:
        status = READ_ONLY
    else:
        status = BLOCKED
    return Subscription(status, access_until, days_left, read_only_from, blocked_from)


def add_months(day: date, months: int) -> date:
    """Kalendar oylar: 31-yanvar + 1 oy = 28/29-fevral."""
    month_index = day.month - 1 + months
    year, month = day.year + month_index // 12, month_index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def record_payment(center, *, months, amount, method="manual", plan=None, comment="", user=None, today=None):
    """
    Obuna to'lovini qayd etadi va muddatni uzaytiradi. Yangi davr joriy muddat tugagan kundan
    (sinov davri ham hisobga olinadi) yoki bugundan, qaysi biri keyin bo'lsa, boshlanadi: oldindan to'lov
    sinov yoki to'langan kunlarni yo'qotmaydi.
    """
    from .models import Center, SubscriptionPayment

    if months < 1:
        raise ValidationError("Kamida 1 oy.")
    today = today or timezone.localdate()
    with transaction.atomic():
        locked = Center.objects.select_for_update().get(pk=center.pk)
        plan = plan or locked.plan
        if plan is None:
            raise ValidationError("Markazga tarif biriktirilmagan.")
        current_end = max([d for d in (locked.trial_ends_at, locked.paid_until) if d] or [today - timedelta(days=1)])
        start = max(today, current_end + timedelta(days=1))
        end = add_months(start, months) - timedelta(days=1)
        payment = SubscriptionPayment.objects.create(
            center=locked, plan=plan, months=months, amount=amount, method=method,
            period_start=start, period_end=end, comment=comment, created_by=user,
        )
        locked.plan = plan
        locked.paid_until = end
        locked.is_active = True
        locked.save(update_fields=["plan", "paid_until", "is_active"])
    center.plan, center.paid_until, center.is_active = locked.plan, locked.paid_until, True
    return payment


# --- to'lov so'rovlari (markaz "to'ladim" deydi, egasi tasdiqlaydi) ------------------------

MONTH_CHOICES = (1, 3, 6, 12)


def create_payment_request(center, *, plan, months, payer_name, payer_phone, note=""):
    """Markaz direktori to'lov qilganini bildiradi. Bitta markazda bir vaqtda bitta kutilayotgan so'rov."""
    from .models import PaymentRequest

    if months not in MONTH_CHOICES:
        raise ValidationError("Oylar soni noto'g'ri.")
    if not plan.is_active:
        raise ValidationError("Bu tarif hozir sotuvda emas.")
    with transaction.atomic():
        if PaymentRequest.objects.select_for_update().filter(center=center, status=PaymentRequest.Status.NEW).exists():
            raise ValidationError("Oldingi to'lov so'rovingiz hali ko'rib chiqilmoqda.")
        request = PaymentRequest.objects.create(
            center=center, plan=plan, months=months, amount=plan.price_month * months,
            payer_name=payer_name.strip()[:120], payer_phone=payer_phone.strip()[:30], note=note.strip()[:255],
        )
    from . import notify

    amount = f"{request.amount:,}".replace(",", " ")
    lines = [
        f"To'lov so'rovi: {center.name} ({center.schema_name})",
        f"Tarif: {plan.name}, {months} oy — {amount} so'm",
        f"To'lovchi: {request.payer_name}, {request.payer_phone}",
    ]
    if request.note:
        lines.append(f"Izoh: {request.note}")
    lines.append("Tasdiqlash: crm.yurtora.uz/panel/requests/")
    notify.notify_owner("\n".join(lines))
    return request


def confirm_payment_request(request, *, user=None, amount=None, today=None):
    """Egasi pulni ko'rdi: to'lov qayd etiladi va muddat uzayadi (record_payment)."""
    from .models import PaymentRequest

    with transaction.atomic():
        locked = PaymentRequest.objects.select_for_update().get(pk=request.pk)
        if locked.status != PaymentRequest.Status.NEW:
            raise ValidationError("So'rov allaqachon ko'rib chiqilgan.")
        payment = record_payment(
            locked.center, months=locked.months, amount=locked.amount if amount is None else amount,
            plan=locked.plan, comment=f"So'rov #{locked.pk}: {locked.payer_name} {locked.payer_phone} {locked.note}".strip()[:255],
            user=user, today=today,
        )
        locked.status = PaymentRequest.Status.CONFIRMED
        locked.payment = payment
        locked.resolved_at = timezone.now()
        locked.save(update_fields=["status", "payment", "resolved_at"])
    return payment


def reject_payment_request(request, *, reason=""):
    from .models import PaymentRequest

    updated = PaymentRequest.objects.filter(pk=request.pk, status=PaymentRequest.Status.NEW).update(
        status=PaymentRequest.Status.REJECTED, reject_reason=reason.strip()[:255], resolved_at=timezone.now()
    )
    if not updated:
        raise ValidationError("So'rov allaqachon ko'rib chiqilgan.")


def end_subscription_now(center, today=None):
    """Obunani darhol tugatish (egasi): muddat kechagacha qisqartiriladi, markaz ertasidan emas — hozir o'chadi."""
    from .models import Center

    yesterday = (today or timezone.localdate()) - timedelta(days=1)
    Center.objects.filter(pk=center.pk).update(
        trial_ends_at=min(center.trial_ends_at, yesterday) if center.trial_ends_at else None,
        paid_until=min(center.paid_until, yesterday) if center.paid_until else yesterday,
    )
    center.refresh_from_db()


# --- tarif limitlari -------------------------------------------------------------


def current_center():
    """So'rov markazi (CenterMiddleware ulanishga o'rnatadi). Markaz bo'lmasa None."""
    tenant = getattr(connection, "tenant", None)
    return tenant if getattr(tenant, "pk", None) else None


def _plan():
    center = current_center()
    return center.plan if center is not None else None


def usage():
    """Joriy markazda: faol o'quvchilar va faol foydalanuvchilar soni."""
    from apps.accounts.models import User
    from apps.students.models import Student

    return {
        "students": Student.objects.exclude(status=Student.Status.ARCHIVED).count(),
        "users": User.objects.filter(is_active=True).count(),
    }


def check_students_limit(adding: int = 1) -> None:
    plan = _plan()
    if plan is None or plan.max_students is None:
        return
    current = usage()["students"]
    if current + adding > plan.max_students:
        raise ValidationError(
            f"«{plan.name}» tarifida {plan.max_students} ta faol o'quvchi mumkin (hozir {current} ta). "
            "Tarifni oshiring yoki keraksiz o'quvchilarni arxivlang."
        )


def check_users_limit(adding: int = 1) -> None:
    plan = _plan()
    if plan is None or plan.max_users is None:
        return
    current = usage()["users"]
    if current + adding > plan.max_users:
        raise ValidationError(
            f"«{plan.name}» tarifida {plan.max_users} ta faol foydalanuvchi mumkin (hozir {current} ta). "
            "Tarifni oshiring yoki keraksiz foydalanuvchini o'chiring."
        )
