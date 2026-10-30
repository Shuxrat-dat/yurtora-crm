"""Yurtora egasi uchun alohida Telegram bot: barcha markazlar statistikasi, obunalar, demo buyurtmalar.

Faqat YURTORA_OWNER_TELEGRAM_CHAT_ID dagi chatlarga javob beradi (vergul bilan bir nechta).
Begona chatga hech qanday ma'lumot chiqmaydi: faqat uning o'z chat ID si (sozlash uchun).

Buyruqlar: /stats (bugun), /week (7 kun), /centers (obunalar), /demo (yangi buyurtmalar).
Har kuni YURTORA_OWNER_DIGEST_HOUR da kunlik hisobot o'zi keladi.
"""

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import Count, Q, Sum
from django.utils import timezone
from django_tenants.utils import get_public_schema_name, schema_context

from apps.notifications import telegram

from . import billing
from .models import Center, DemoRequest, OwnerDigestLog, Suggestion

logger = logging.getLogger(__name__)


def token():
    return settings.YURTORA_OWNER_BOT_TOKEN


def is_configured():
    return bool(token() and owner_chat_ids())


def owner_chat_ids() -> set[int]:
    ids = set()
    for part in str(settings.YURTORA_OWNER_TELEGRAM_CHAT_ID or "").replace(";", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            ids.add(int(part))
    return ids


def money(amount):
    return f"{int(amount or 0):,}".replace(",", " ")


@dataclass
class CenterStats:
    name: str
    schema: str
    status: str
    days_left: int | None
    students: int = 0
    new_students: int = 0
    paid: int = 0
    payments: int = 0
    lessons: int = 0
    lessons_held: int = 0
    marked: int = 0
    came: int = 0
    debt: int = 0
    errors: list = field(default_factory=list)


def center_stats(center, start: date, end: date) -> CenterStats:
    """Bitta markaz: [start, end] kunlari bo'yicha ko'rsatkichlar (o'z sxemasida hisoblanadi)."""
    from apps.attendance.models import Attendance
    from apps.finance.models import LedgerEntry
    from apps.schedule.models import Lesson
    from apps.students.models import Student

    sub = billing.subscription(center)
    stats = CenterStats(center.name, center.schema_name, sub.label, sub.days_left)
    with schema_context(center.schema_name):
        stats.students = Student.objects.filter(status="active").count()
        stats.new_students = Student.objects.filter(joined_date__gte=start, joined_date__lte=end).count()
        pay = LedgerEntry.objects.filter(kind="payment", entry_date__gte=start, entry_date__lte=end).aggregate(
            total=Sum("amount"), count=Count("id")
        )
        stats.paid, stats.payments = pay["total"] or 0, pay["count"]
        lessons = Lesson.objects.filter(date__gte=start, date__lte=end).exclude(status="cancelled").aggregate(
            total=Count("id"), held=Count("id", filter=Q(status="held"))
        )
        stats.lessons, stats.lessons_held = lessons["total"], lessons["held"]
        att = Attendance.objects.filter(lesson__date__gte=start, lesson__date__lte=end).aggregate(
            total=Count("id"), came=Count("id", filter=Q(status__in=["present", "late"]))
        )
        stats.marked, stats.came = att["total"], att["came"]
        balances = (
            LedgerEntry.objects.values("student", "group").annotate(total=Sum("amount")).filter(total__lt=0)
        )
        stats.debt = -sum(row["total"] for row in balances)
    return stats


def all_stats(start: date, end: date) -> list[CenterStats]:
    with schema_context(get_public_schema_name()):
        centers = list(Center.objects.exclude(schema_name=get_public_schema_name()).order_by("name"))
    result = []
    for center in centers:
        try:
            result.append(center_stats(center, start, end))
        except Exception:  # noqa: BLE001 - bitta markaz xatosi hisobotni to'xtatmasin
            logger.exception("Egasi statistikasi: %s", center.schema_name)
            result.append(CenterStats(center.name, center.schema_name, "xato", None, errors=["hisoblab bo'lmadi"]))
    return result


def _percent(part, total):
    return f"{round(part * 100 / total)}%" if total else "—"


def stats_text(start: date, end: date, title: str) -> str:
    rows = all_stats(start, end)
    lines = [title, ""]
    for s in rows:
        if s.errors:
            lines.append(f"• {s.name}: hisoblab bo'lmadi")
            continue
        lines.append(f"• {s.name} ({s.schema}) — {s.status}")
        lines.append(
            f"  O'quvchilar {s.students} (+{s.new_students}) · to'lov {money(s.paid)} so'm ({s.payments} ta)"
        )
        lines.append(
            f"  Darslar {s.lessons_held}/{s.lessons} · davomat {_percent(s.came, s.marked)} · qarz {money(s.debt)} so'm"
        )
    ok = [s for s in rows if not s.errors]
    lines += [
        "",
        f"JAMI: {len(rows)} markaz · {sum(s.students for s in ok)} o'quvchi (+{sum(s.new_students for s in ok)})",
        f"To'lovlar: {money(sum(s.paid for s in ok))} so'm · qarzlar: {money(sum(s.debt for s in ok))} so'm",
    ]
    with schema_context(get_public_schema_name()):
        demos = DemoRequest.objects.filter(created_at__date__gte=start, created_at__date__lte=end).count()
        new_demos = DemoRequest.objects.filter(status=DemoRequest.Status.NEW).count()
        suggestions = Suggestion.objects.filter(created_at__date__gte=start, created_at__date__lte=end).count()
    lines.append(f"Demo buyurtmalar: {demos} (javob kutayotgan: {new_demos}) · takliflar: {suggestions}")
    expiring = [s for s in ok if s.days_left is not None and s.days_left <= 5]
    if expiring:
        lines += ["", "Obunasi tugayapti:"]
        lines += [f"• {s.name}: {s.days_left} kun" for s in sorted(expiring, key=lambda s: s.days_left)]
    return "\n".join(lines)


def centers_text() -> str:
    with schema_context(get_public_schema_name()):
        centers = list(Center.objects.exclude(schema_name=get_public_schema_name()).order_by("name"))
    if not centers:
        return "Markazlar hali yo'q."
    lines = ["Markazlar va obunalar:", ""]
    for center in centers:
        sub = billing.subscription(center)
        until = f", {sub.access_until:%d.%m.%Y} gacha ({sub.days_left} kun)" if sub.access_until else ""
        lines.append(f"• {center.name} ({center.schema_name}) — {sub.label}{until}")
    return "\n".join(lines)


def demo_text(limit=10) -> str:
    with schema_context(get_public_schema_name()):
        items = list(DemoRequest.objects.filter(status=DemoRequest.Status.NEW)[:limit])
    if not items:
        return "Javob kutayotgan demo buyurtma yo'q."
    lines = ["Yangi demo buyurtmalar:", ""]
    for d in items:
        lines.append(f"• {d.center_name} — {d.name}, {d.phone} ({timezone.localtime(d.created_at):%d.%m %H:%M})")
    return "\n".join(lines)


HELP = (
    "Yurtora egasi boti.\n"
    "/stats — bugungi ko'rsatkichlar\n"
    "/week — oxirgi 7 kun\n"
    "/centers — markazlar va obunalar\n"
    "/demo — yangi demo buyurtmalar\n"
    "Har kuni kechqurun kunlik hisobot o'zi keladi."
)


def handle_update(update):
    """(chat_id, javob) yoki None. Faqat egasi chatlari; boshqalarga faqat o'z chat ID si."""
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = (message.get("text") or "").strip()
    if not chat_id or chat.get("type") != "private" or not text.startswith("/"):
        return None
    if chat_id not in owner_chat_ids():
        return chat_id, f"Bu bot faqat Yurtora egasi uchun. Sizning chat ID: {chat_id}"
    command = text.split()[0].split("@")[0].lower()
    today = timezone.localdate()
    if command == "/stats":
        return chat_id, stats_text(today, today, f"Bugun, {today:%d.%m.%Y}")
    if command == "/week":
        start = today - timedelta(days=6)
        return chat_id, stats_text(start, today, f"Oxirgi 7 kun: {start:%d.%m} – {today:%d.%m.%Y}")
    if command == "/centers":
        return chat_id, centers_text()
    if command == "/demo":
        return chat_id, demo_text()
    return chat_id, HELP


def send_to_owner(text):
    """Barcha egasi chatlariga; yuborilganlar soni. Xato jurnalga yoziladi, hech qachon ko'tarilmaydi."""
    sent = 0
    for chat_id in owner_chat_ids():
        try:
            telegram.send_message(chat_id, text, token=token())
            sent += 1
        except telegram.TelegramError as exc:
            logger.warning("Egasiga xabar yuborilmadi: %s", exc)
    return sent


def maybe_send_daily(now=None):
    """Har kuni bir marta, YURTORA_OWNER_DIGEST_HOUR dan keyin: kunlik hisobot. Yuborilgan bo'lsa True."""
    if not is_configured():
        return False
    local_now = (now or timezone.now()).astimezone(ZoneInfo(settings.TIME_ZONE))
    if local_now.hour < settings.YURTORA_OWNER_DIGEST_HOUR:
        return False
    day = local_now.date()
    with schema_context(get_public_schema_name()):
        _, created = OwnerDigestLog.objects.get_or_create(day=day)
    if not created:
        return False
    send_to_owner(stats_text(day, day, f"Kunlik hisobot, {day:%d.%m.%Y}"))
    return True
