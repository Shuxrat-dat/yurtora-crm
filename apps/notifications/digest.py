"""Haftalik qarz ro'yxati: xodim chati, tasdiqlash tugmalari va jadval bo'yicha yuborish."""

import secrets
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import services, telegram
from .models import AdminChat, AdminChatToken, DebtDigest

ADMIN_PREFIX = "admin_"
TOKEN_TTL_HOURS = 72
DIGEST_TTL_HOURS = 24
LOCAL_TZ = ZoneInfo("Asia/Tashkent")


def active_admin_chats():
    """Faol xodim chatlari: faqat hozir ham faol direktor/administrator bo'lgan foydalanuvchilarniki.

    Xodim o'chirilsa yoki o'qituvchi rolga o'tkazilsa, uning Telegram chati qarz ro'yxatini olmaydi
    va tasdiqlash tugmalarini bosa olmaydi.
    """
    from apps.accounts.models import User

    return AdminChat.objects.filter(
        is_active=True,
        user__is_active=True,
        user__role__in=[User.Role.DIRECTOR, User.Role.ADMINISTRATOR],
    )


def deactivate_chats_of(user):
    """Foydalanuvchining barcha xodim chatlarini o'chiradi (o'chirilganda yoki roli o'zgarganda)."""
    count = 0
    for chat in AdminChat.objects.filter(user=user, is_active=True):
        chat.is_active = False
        chat.save(update_fields=["is_active"])  # save, а не update(): изменение попадает в журнал действий
        count += 1
    return count


# --- Xodim chatini ulash -------------------------------------------------------


def create_admin_token(user):
    return AdminChatToken.objects.create(
        user=user,
        token=secrets.token_urlsafe(24),
        expires_at=timezone.now() + timedelta(hours=TOKEN_TTL_HOURS),
    )


def admin_deep_link(token):
    username = settings.TELEGRAM_BOT_USERNAME.lstrip("@")
    from .routing import payload_for

    return f"https://t.me/{username}?start={payload_for(ADMIN_PREFIX + token.token)}" if username else ""


def link_admin_chat(token_value, chat_id, username=""):
    """Tokenni ishlatib xodim chatini ulaydi. Xodim (User) yoki None qaytaradi."""
    with transaction.atomic():
        token = (
            AdminChatToken.objects.select_for_update()
            .select_related("user")
            .filter(token=token_value, used_at__isnull=True, expires_at__gt=timezone.now())
            .first()
        )
        if token is None:
            return None
        token.used_at = timezone.now()
        token.save(update_fields=["used_at"])
        AdminChat.objects.update_or_create(
            chat_id=chat_id,
            defaults={"user": token.user, "username": username[:64], "is_active": True},
        )
        return token.user


def deactivate_admin_chat(chat_id):
    return AdminChat.objects.filter(chat_id=chat_id, is_active=True).update(is_active=False)


# --- Ro'yxat ---------------------------------------------------------------------


def sendable_rows():
    """Qarzdorlar: Telegram ulangan va yaqinda eslatma olmaganlar."""
    rows = services.debts_by_student()
    ids = [r["student"].pk for r in rows]
    linked = services.students_with_telegram(ids)
    last = services.last_reminder_at(ids)
    return [
        r
        for r in rows
        if r["student"].pk in linked and not services.cooldown_active(last.get(r["student"].pk))
    ]


def create_digest():
    """Ro'yxat yaratadi. Yuboriladigan hech kim bo'lmasa, hafta 'ko'rilgan' deb belgilanadi
    (CANCELLED), shunda bot har 30 soniyada qayta hisoblamaydi."""
    rows = sendable_rows()
    digest = DebtDigest.objects.create(
        student_ids=[r["student"].pk for r in rows],
        total_debt=sum(r["total"] for r in rows),
        status=DebtDigest.Status.PENDING if rows else DebtDigest.Status.CANCELLED,
    )
    return digest, rows


def digest_text(digest, rows, top=8):
    lines = [
        "Haftalik qarz ro'yxati",
        f"Telegram ulangan va yaqinda eslatma olmagan qarzdorlar: {len(rows)} ta",
        f"Jami qarz: {services.money(digest.total_debt)} so'm",
        "",
        "Eng katta qarzlar:",
    ]
    lines += [
        f"{i}. {r['student'].full_name}: {services.money(r['total'])} so'm"
        for i, r in enumerate(rows[:top], start=1)
    ]
    if len(rows) > top:
        lines.append(f"... va yana {len(rows) - top} ta")
    lines += ["", "Ota-onalarga eslatma yuborilsinmi?"]
    return "\n".join(lines)


def digest_keyboard(digest, count):
    from .routing import callback_for

    return {
        "inline_keyboard": [
            [
                {"text": f"Yuborish ({count})", "callback_data": callback_for("send", digest.pk)},
                {"text": "Bekor qilish", "callback_data": callback_for("cancel", digest.pk)},
            ]
        ]
    }


def notify_admins(digest, rows):
    """Ro'yxatni faol xodim chatlariga yuboradi; yetkazilganlar sonini qaytaradi."""
    text = digest_text(digest, rows)
    keyboard = digest_keyboard(digest, len(rows))
    delivered = 0
    for chat in active_admin_chats():
        try:
            telegram.send_message(chat.chat_id, text, reply_markup=keyboard)
            delivered += 1
        except telegram.TelegramError as exc:
            if exc.blocked:
                chat.is_active = False
                chat.save(update_fields=["is_active"])
    return delivered


def resolve_digest(digest_id, chat_id, action):
    """Tasdiqlash yoki bekor qilish. Holat bir marta o'zgaradi (qayta bosish ishlamaydi)."""
    with transaction.atomic():
        digest = DebtDigest.objects.select_for_update().filter(pk=digest_id).first()
        if digest is None:
            return "Ro'yxat topilmadi."
        if digest.status != DebtDigest.Status.PENDING:
            return f"Bu ro'yxat allaqachon qayta ishlangan: {digest.get_status_display()}."
        now = timezone.now()
        digest.resolved_at = now
        digest.resolved_by_chat = chat_id
        if digest.created_at < now - timedelta(hours=DIGEST_TTL_HOURS):
            digest.status = DebtDigest.Status.EXPIRED
            digest.save()
            return "Ro'yxat muddati tugagan (24 soat). Yangisini kuting."
        if action == "cancel":
            digest.status = DebtDigest.Status.CANCELLED
            digest.save()
            return "Bekor qilindi. Hech kimga yuborilmadi."
        digest.status = DebtDigest.Status.SENT
        digest.save()

        # Ro'yxat tuzilgandan keyin holat o'zgargan bo'lishi mumkin: hozirgi holat qayta tekshiriladi.
        # Navbatga qo'yish holat o'zgarishi bilan bir tranzaksiyada: tasdiqlangan ro'yxat yarim yo'lda yo'qolmaydi.
        chat = AdminChat.objects.filter(chat_id=chat_id).select_related("user").first()
        user = chat.user if chat else None
        current = {r["student"].pk: r for r in sendable_rows()}
        queued_students = skipped = 0
        for student_id in digest.student_ids:
            row = current.get(student_id)
            if row is None:
                skipped += 1
                continue
            queued_students += 1 if services.queue_debt_reminder(row["student"], row["groups"], user) else 0
    return (
        f"Navbatga qo'yildi: {queued_students} ta o'quvchi (bot yuboradi, bir necha daqiqa). "
        f"O'tkazib yuborildi (to'lagan yoki yaqinda yuborilgan): {skipped}."
    )


def handle_callback(callback):
    """Inline tugma bosilishi. Faqat ulangan xodim chatlaridan qabul qilinadi."""
    callback_id = callback.get("id")
    message = callback.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if chat.get("type") != "private" or not chat_id:
        return
    if not active_admin_chats().filter(chat_id=chat_id).exists():
        telegram.answer_callback_query(callback_id, "Ruxsat yo'q")
        return
    action, _, raw = (callback.get("data") or "").partition(":")
    if action not in ("send", "cancel") or not raw.isdigit():
        telegram.answer_callback_query(callback_id)
        return

    result = resolve_digest(int(raw), chat_id, action)
    try:
        telegram.answer_callback_query(callback_id)
        telegram.remove_buttons(chat_id, message.get("message_id"))
    except telegram.TelegramError:
        pass  # natija xabari baribir yuboriladi
    telegram.send_message(chat_id, result)


def maybe_send_weekly_digest(now=None):
    """Jadval bo'yicha haftada bir marta ro'yxat yuboradi. DebtDigest yoki None qaytaradi."""
    if not telegram.is_configured():
        return None
    local_now = (now or timezone.now()).astimezone(LOCAL_TZ)
    week_start = (local_now - timedelta(days=local_now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    scheduled = week_start + timedelta(
        days=settings.TELEGRAM_DIGEST_WEEKDAY, hours=settings.TELEGRAM_DIGEST_HOUR
    )
    if local_now < scheduled:
        return None
    if DebtDigest.objects.filter(created_at__gte=week_start).exists():
        return None
    if not active_admin_chats().exists():
        return None
    digest, rows = create_digest()
    if digest.status == DebtDigest.Status.PENDING:
        notify_admins(digest, rows)
    return digest
