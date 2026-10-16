"""Telegram Bot API bilan minimal mijoz (faqat standart kutubxona: urllib)."""

import json
import urllib.error
import urllib.request

from django.conf import settings

API_URL = "https://api.telegram.org/bot{token}/{method}"


class TelegramError(Exception):
    """Telegram so'rovi xato bilan tugadi. `blocked=True` — foydalanuvchi botni bloklagan."""

    def __init__(self, message, blocked=False):
        super().__init__(message)
        self.blocked = blocked


def is_configured():
    return bool(settings.TELEGRAM_BOT_TOKEN)


def _call(method, payload, timeout=15):
    if not is_configured():
        raise TelegramError("TELEGRAM_BOT_TOKEN sozlanmagan.")
    request = urllib.request.Request(
        API_URL.format(token=settings.TELEGRAM_BOT_TOKEN, method=method),
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            body = json.load(response)
    except urllib.error.HTTPError as exc:
        # 403 — bot bloklangan yoki chat yopilgan. Xabarda token (URL) ko'rsatilmaydi.
        raise TelegramError(f"Telegram HTTP {exc.code}", blocked=exc.code == 403) from None
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise TelegramError(f"Telegram'ga ulanib bo'lmadi: {type(exc).__name__}") from None
    if not body.get("ok"):
        raise TelegramError(str(body.get("description", "noma'lum xato"))[:200])
    return body["result"]


def send_message(chat_id, text, reply_markup=None):
    """Oddiy matn (parse_mode yo'q — ism va summalardagi belgilar xabarni buzmaydi)."""
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return _call("sendMessage", payload)


def answer_callback_query(callback_id, text=""):
    return _call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})


def remove_buttons(chat_id, message_id):
    """Tugmalarni olib tashlaydi (qayta bosib bo'lmasligi uchun)."""
    return _call(
        "editMessageReplyMarkup",
        {"chat_id": chat_id, "message_id": message_id, "reply_markup": {"inline_keyboard": []}},
    )


def get_updates(offset=None, timeout=30):
    payload = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
    if offset is not None:
        payload["offset"] = offset
    return _call("getUpdates", payload, timeout=timeout + 10)
