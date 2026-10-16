"""Bitta Yurtora boti barcha markazlar uchun: Telegram update'ni kerakli markaz sxemasiga yo'naltiradi.

Havola va tugmalarda markaz kodi (sxema nomi) bor:
- /start <sxema>_<token>          — ota-onani o'quvchiga ulash
- /start <sxema>_admin_<token>    — xodim chatini ulash (haftalik ro'yxat)
- tugma: <sxema>:send:<id> yoki <sxema>:cancel:<id>
Sxema nomida "_" bo'lmaydi (faqat harf va raqam), shuning uchun birinchi "_" — ajratgich.
"""

import logging

from django.db import connection
from django_tenants.utils import schema_context

from apps.tenants.services import active_center_schemas, center_schema_is_active

from . import digest, services, telegram

logger = logging.getLogger(__name__)

INVALID_LINK = "Havola yaroqsiz yoki muddati tugagan. Administratordan yangi havola so'rang."


def payload_for(token_value: str) -> str:
    """Joriy markaz sxemasi bilan /start parametri."""
    return f"{connection.schema_name}_{token_value}"


def callback_for(action: str, digest_id: int) -> str:
    return f"{connection.schema_name}:{action}:{digest_id}"


def _split_payload(argument: str):
    schema, sep, rest = argument.partition("_")
    if not sep or not rest or not center_schema_is_active(schema):
        return None, None
    return schema, rest


def handle_update(update):
    """Xabar update'i: (chat_id, javob) yoki None. Markazga tegishli buyruqlar o'sha markaz sxemasida bajariladi."""
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    text = (message.get("text") or "").strip()
    chat_id = chat.get("id")
    if not chat_id or not text.startswith("/") or chat.get("type") != "private":
        return None  # faqat shaxsiy chatdagi buyruqlar (guruhlarga qarz ma'lumoti chiqmasin)

    command, _, argument = text.partition(" ")
    command = command.split("@")[0].lower()
    argument = argument.strip()

    if command == "/start" and argument:
        schema, rest = _split_payload(argument)
        if schema is None:
            return chat_id, INVALID_LINK
        local = dict(update, message=dict(message, text=f"/start {rest}"))
        with schema_context(schema):
            return services.handle_update(local)

    if command == "/stop":
        count = 0
        for schema in active_center_schemas():
            with schema_context(schema):
                count += services.unlink_chat(chat_id) + digest.deactivate_admin_chat(chat_id)
        return chat_id, "Bildirishnomalar o'chirildi." if count else "Faol ulanish topilmadi."

    return chat_id, services.help_text()  # ommaviy sxemada: umumiy Yurtora boti matni


def handle_callback(callback):
    """Haftalik ro'yxat tugmasi: <sxema>:<amal>:<id> -> o'sha markazda qayta ishlanadi."""
    schema, sep, rest = (callback.get("data") or "").partition(":")
    if not sep or not center_schema_is_active(schema):
        telegram.answer_callback_query(callback.get("id"))
        return
    with schema_context(schema):
        digest.handle_callback(dict(callback, data=rest))


def send_pending_everywhere() -> bool:
    """Har bir markaz navbatini yuboradi. Biror markazda xabar qolgan bo'lsa True."""
    backlog = False
    for schema in active_center_schemas():
        with schema_context(schema):
            services.process_pending_notifications()
            backlog = backlog or services.pending_count() > 0
    return backlog


def weekly_digest_everywhere():
    for schema in active_center_schemas():
        with schema_context(schema):
            try:
                digest.maybe_send_weekly_digest()
            except Exception:  # bitta markaz xatosi boshqalarini to'xtatmasin
                logger.exception("Haftalik ro'yxat xato: %s", schema)
