"""Yurtora egasiga xabar: Telegram sozlangan bo'lsa yuboriladi, aks holda faqat jurnalga yoziladi (so'rovni hech qachon buzmaydi)."""

import logging
import threading

from django.conf import settings

logger = logging.getLogger(__name__)


def owner_telegram_ready():
    return bool(settings.TELEGRAM_BOT_TOKEN and settings.YURTORA_OWNER_TELEGRAM_CHAT_ID)


def _send(text):
    from apps.notifications.telegram import TelegramError, send_message

    try:
        send_message(settings.YURTORA_OWNER_TELEGRAM_CHAT_ID, text)
    except TelegramError as exc:
        logger.warning("Egasiga Telegram xabari yuborilmadi: %s", exc)
    except Exception:  # noqa: BLE001 - xabar yuborish hech qachon buyurtmani buzmasligi kerak
        logger.exception("Egasiga xabar yuborishda kutilmagan xato")


def notify_owner(text):
    """Orqa oqimda yuboradi: foydalanuvchi Telegram javobini kutib o'tirmaydi."""
    if not owner_telegram_ready():
        logger.info("Telegram sozlanmagan, xabar yuborilmadi: %s", text.splitlines()[0])
        return False
    threading.Thread(target=_send, args=(text,), daemon=True).start()
    return True
