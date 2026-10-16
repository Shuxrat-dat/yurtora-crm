"""Telegram bildirishnomalari: ulash, qarz eslatmasi, bot buyruqlari."""

import secrets
import time
from collections import OrderedDict
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core.branding import center_name
from apps.finance.services import get_debtors

from . import telegram
from .models import NotificationLog, TelegramContact, TelegramLinkToken

TOKEN_TTL_HOURS = 72


def money(amount):
    return f"{abs(amount):,}".replace(",", " ")


# --- Ulash ---------------------------------------------------------------


def create_link_token(student, user=None):
    return TelegramLinkToken.objects.create(
        student=student,
        token=secrets.token_urlsafe(24),
        created_by=user,
        expires_at=timezone.now() + timedelta(hours=TOKEN_TTL_HOURS),
    )


def deep_link(token):
    username = settings.TELEGRAM_BOT_USERNAME.lstrip("@")
    return f"https://t.me/{username}?start={token.token}" if username else ""


def link_chat(token_value, chat_id, username=""):
    """Tokenni ishlatib chatni o'quvchiga bog'laydi. Yaroqsiz bo'lsa None qaytaradi."""
    with transaction.atomic():
        token = (
            TelegramLinkToken.objects.select_for_update()
            .select_related("student")
            .filter(token=token_value, used_at__isnull=True, expires_at__gt=timezone.now())
            .first()
        )
        if token is None:
            return None
        token.used_at = timezone.now()
        token.save(update_fields=["used_at"])
        try:
            with transaction.atomic():
                contact, created = TelegramContact.objects.get_or_create(
                    student=token.student,
                    chat_id=chat_id,
                    defaults={"username": username[:64]},
                )
        except IntegrityError:
            contact = TelegramContact.objects.get(student=token.student, chat_id=chat_id)
            created = False
        if not created and not contact.is_active:
            contact.is_active = True
            contact.save(update_fields=["is_active"])
        return token.student


def unlink_chat(chat_id):
    return TelegramContact.objects.filter(chat_id=chat_id, is_active=True).update(
        is_active=False
    )


# --- Qarz eslatmasi --------------------------------------------------------


def debts_by_student():
    """[{student, total (musbat), groups: [(guruh nomi, summa)]}], eng katta qarz birinchi."""
    grouped = OrderedDict()
    for item in get_debtors():
        student = item["student"]
        if student is None:
            continue
        entry = grouped.setdefault(student.pk, {"student": student, "total": 0, "groups": []})
        amount = abs(item["balance"])
        entry["total"] += amount
        entry["groups"].append((item["group"].name if item["group"] else "—", amount))
    return sorted(grouped.values(), key=lambda e: -e["total"])


def build_debt_message(student, groups):
    lines = [
        f"Assalomu alaykum! «{center_name()}»: to'lov eslatmasi.",
        f"{student.full_name} bo'yicha to'lanmagan qarzdorlik mavjud:",
    ]
    lines += [f"• {name}: {money(amount)} so'm" for name, amount in groups]
    if len(groups) > 1:
        lines.append(f"Jami: {money(sum(a for _, a in groups))} so'm")
    lines.append(
        "Iltimos, to'lovni amalga oshiring. Savollar bo'lsa, administratorga murojaat qiling."
    )
    return "\n".join(lines)


def last_reminder_at(student_ids):
    """{student_id: oxirgi eslatma vaqti} — yuborilgan va navbatdagilar (qayta yuborilmasligi uchun)."""
    result = {}
    logs = NotificationLog.objects.filter(
        student_id__in=student_ids,
        kind=NotificationLog.Kind.DEBT,
        status__in=[NotificationLog.Status.SENT, NotificationLog.Status.PENDING],
    ).order_by("created_at")
    for log in logs:
        result[log.student_id] = log.created_at
    return result


def cooldown_active(sent_at):
    if sent_at is None:
        return False
    return sent_at > timezone.now() - timedelta(days=settings.TELEGRAM_REMINDER_COOLDOWN_DAYS)


def students_with_telegram(student_ids):
    return set(
        TelegramContact.objects.filter(student_id__in=student_ids, is_active=True).values_list(
            "student_id", flat=True
        )
    )


def send_debt_reminder(student, groups, user=None):
    """O'quvchining barcha faol chatlariga yuboradi. (yuborildi, xato) sonini qaytaradi."""
    text = build_debt_message(student, groups)
    sent = failed = 0
    for contact in student.telegram_contacts.filter(is_active=True):
        try:
            telegram.send_message(contact.chat_id, text)
            status, error = NotificationLog.Status.SENT, ""
            sent += 1
        except telegram.TelegramError as exc:
            status, error = NotificationLog.Status.FAILED, str(exc)[:255]
            failed += 1
            if exc.blocked:
                contact.is_active = False
                contact.save(update_fields=["is_active"])
        NotificationLog.objects.create(
            student=student,
            chat_id=contact.chat_id,
            kind=NotificationLog.Kind.DEBT,
            text=SENT_TEXT_PLACEHOLDER,  # ism va summa bazada saqlanmaydi (shaxsiy ma'lumot)
            status=status,
            error=error,
            sent_by=user,
        )
    return sent, failed


def queue_debt_reminder(student, groups, user=None):
    """Eslatmani yuborish navbatiga qo'yadi (veb-so'rov Telegram'ni kutmaydi). Navbatga qo'yilgan chatlar soni."""
    text = build_debt_message(student, groups)
    queued = 0
    for contact in student.telegram_contacts.filter(is_active=True):
        try:
            with transaction.atomic():
                NotificationLog.objects.create(
                    student=student,
                    chat_id=contact.chat_id,
                    kind=NotificationLog.Kind.DEBT,
                    text=text,
                    status=NotificationLog.Status.PENDING,
                    sent_by=user,
                )
            queued += 1
        except IntegrityError:
            pass  # shu chat uchun allaqachon navbatda
    return queued


SENT_TEXT_PLACEHOLDER = "Qarz eslatmasi (matn saqlanmaydi)"


def process_pending_notifications(limit=25, pause=0.05):
    """Navbatdagi eslatmalarni yuboradi (bot chaqiradi). Qayta ishlangan xabarlar soni.

    Har xabar o'z tranzaksiyasida: bot to'xtasa, yuborilmagan xabarlar navbatda qoladi va keyin yuboriladi.
    """
    done = 0
    ids = list(
        NotificationLog.objects.filter(status=NotificationLog.Status.PENDING)
        .order_by("id")
        .values_list("id", flat=True)[:limit]
    )
    for log_id in ids:
        with transaction.atomic():
            log = (
                NotificationLog.objects.select_for_update(skip_locked=True)
                .filter(pk=log_id, status=NotificationLog.Status.PENDING)
                .first()
            )
            if log is None:
                continue
            try:
                telegram.send_message(log.chat_id, log.text)
                log.status = NotificationLog.Status.SENT
            except telegram.TelegramError as exc:
                log.status = NotificationLog.Status.FAILED
                log.error = str(exc)[:255]
                if exc.blocked:
                    TelegramContact.objects.filter(student_id=log.student_id, chat_id=log.chat_id).update(
                        is_active=False
                    )
            log.text = SENT_TEXT_PLACEHOLDER  # yuborilgandan keyin ism va summa bazada saqlanmaydi (shaxsiy ma'lumot)
            log.save(update_fields=["status", "error", "text"])
        done += 1
        if pause:
            time.sleep(pause)
    return done


def pending_count():
    return NotificationLog.objects.filter(status=NotificationLog.Status.PENDING).count()


# --- Bot buyruqlari ----------------------------------------------------------

def help_text() -> str:
    return (
        f"Assalomu alaykum! Bu «{center_name()}» boti.\n"
        "Ulanish uchun administratordan olgan havolani bosing.\n"
        "Bildirishnomalarni to'xtatish: /stop"
    )


def handle_update(update):
    """Bitta Telegram update'ni qayta ishlaydi; (chat_id, javob matni) yoki None qaytaradi."""
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    text = (message.get("text") or "").strip()
    chat_id = chat.get("id")
    if not chat_id or not text.startswith("/"):
        return None
    # Faqat shaxsiy chat: guruhlarga qarz ma'lumoti yuborilmasin
    if chat.get("type") != "private":
        return None

    command, _, argument = text.partition(" ")
    command = command.split("@")[0].lower()
    username = (message.get("from") or {}).get("username", "") or ""

    if command == "/start":
        argument = argument.strip()
        if not argument:
            return chat_id, help_text()
        if argument.startswith("admin_"):
            from . import digest

            user = digest.link_admin_chat(argument[len("admin_"):], chat_id, username)
            if user is None:
                return chat_id, "Havola yaroqsiz yoki muddati tugagan. CRM'dan yangisini oling."
            return chat_id, (
                f"Ulandi: {user.get_username()}. Har hafta qarzdorlar ro'yxati shu yerga keladi, "
                "yuborish faqat sizning tasdig'ingizdan keyin.\nTo'xtatish: /stop"
            )
        student = link_chat(argument, chat_id, username)
        if student is None:
            return chat_id, (
                "Havola yaroqsiz yoki muddati tugagan. Administratordan yangi havola so'rang."
            )
        return chat_id, (
            f"Ulandi: {student.full_name}. Bundan buyon shu yerga to'lov eslatmalari keladi.\n"
            "To'xtatish: /stop"
        )
    if command == "/stop":
        from . import digest

        count = unlink_chat(chat_id) + digest.deactivate_admin_chat(chat_id)
        return chat_id, "Bildirishnomalar o'chirildi." if count else "Faol ulanish topilmadi."
    return chat_id, help_text()
