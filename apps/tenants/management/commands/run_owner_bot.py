"""Yurtora egasi boti (long-polling): /stats, /week, /centers, /demo va kunlik hisobot.

Ota-onalar botidan alohida token (YURTORA_OWNER_BOT_TOKEN), alohida jarayon (docker-compose: `owner_bot`).
"""

import logging
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import Error as DatabaseError
from django.db import close_old_connections, connection

from apps.notifications import telegram
from apps.tenants import owner_bot

logger = logging.getLogger(__name__)

RETRY_PAUSE_SECONDS = 5


class Command(BaseCommand):
    help = "Yurtora egasi Telegram boti (long-polling)"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Bir marta so'rab, chiqib ketish (sinov uchun)")

    def handle(self, *args, **options):
        if not owner_bot.token():
            raise CommandError("YURTORA_OWNER_BOT_TOKEN sozlanmagan.")
        if not owner_bot.owner_chat_ids():
            self.stderr.write("YURTORA_OWNER_TELEGRAM_CHAT_ID bo'sh: botga yozing, u sizga chat ID ni aytadi.")
        self.stdout.write("Egasi boti ishga tushdi. To'xtatish: Ctrl+C")
        offset = None
        try:
            while True:
                if not connection.in_atomic_block:
                    close_old_connections()
                try:
                    updates = telegram.get_updates(
                        offset=offset, timeout=0 if options["once"] else 30, token=owner_bot.token()
                    )
                except telegram.TelegramError as exc:
                    self.stderr.write(f"getUpdates xato: {exc}")
                    if options["once"]:
                        raise CommandError(str(exc)) from None
                    time.sleep(RETRY_PAUSE_SECONDS)
                    continue
                for update in updates:
                    offset = update["update_id"] + 1  # egasi buyrug'i qayta yuborilishi mumkin: kutish shart emas
                    self._reply(update)
                self._daily()
                if options["once"]:
                    return
        except KeyboardInterrupt:
            self.stdout.write("To'xtatildi.")

    def _reply(self, update):
        try:
            reply = owner_bot.handle_update(update)
            if reply:
                telegram.send_message(*reply, token=owner_bot.token())
        except telegram.TelegramError as exc:
            self.stderr.write(f"Javob yuborilmadi: {exc}")
        except DatabaseError:
            logger.exception("Egasi boti: ma'lumotlar bazasi xatosi")
            if not connection.in_atomic_block:
                connection.close()
        except Exception:  # bitta xato botni to'xtatmasin
            logger.exception("Egasi boti: update xato")

    def _daily(self):
        try:
            owner_bot.maybe_send_daily()
        except DatabaseError:
            logger.exception("Egasi kunlik hisoboti: ma'lumotlar bazasi xatosi")
            if not connection.in_atomic_block:
                connection.close()
        except Exception:
            logger.exception("Egasi kunlik hisoboti xato")
