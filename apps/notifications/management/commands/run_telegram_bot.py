"""Telegram botni ishga tushiradi (long-polling): /start <token> va /stop buyruqlarini qayta ishlaydi.

Tashqaridan port ochish shart emas. Alohida jarayon sifatida ishlaydi (docker-compose.prod.yml: `bot`).

Bot uzoq yashaydigan jarayon: har aylanishda ma'lumotlar bazasi ulanishi yangilanadi (BD qayta ishga
tushsa yoki ulanish uzilsa, bot o'zi tiklanadi). Xatolar to'liq izi bilan jurnalga yoziladi.
"""

import logging
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import Error as DatabaseError
from django.db import close_old_connections, connection

from apps.notifications import digest, services, telegram

logger = logging.getLogger(__name__)

RETRY_PAUSE_SECONDS = 5
MAX_ATTEMPTS_PER_UPDATE = 3  # BD xatosida bitta update shuncha marta qayta uriniladi, keyin o'tkazib yuboriladi


def refresh_db_connection():
    """Eskirgan yoki uzilgan ulanishni yopadi: keyingi so'rov yangi ulanish ochadi (tranzaksiya ichida tegmaydi)."""
    if not connection.in_atomic_block:
        close_old_connections()


class Command(BaseCommand):
    help = "Telegram bot (long-polling)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--once", action="store_true", help="Bir marta so'rab, chiqib ketish (sinov uchun)"
        )

    def handle(self, *args, **options):
        if not telegram.is_configured():
            raise CommandError("TELEGRAM_BOT_TOKEN sozlanmagan.")

        self.stdout.write("Telegram bot ishga tushdi. To'xtatish: Ctrl+C")
        offset = None
        attempts = {}
        try:
            while True:
                refresh_db_connection()
                backlog = self._send_pending()
                try:
                    # Navbatda xabar qolgan bo'lsa, uzoq kutmaymiz: keyingi aylanishda yana yuboramiz
                    updates = telegram.get_updates(
                        offset=offset, timeout=0 if options["once"] else (2 if backlog else 30)
                    )
                except telegram.TelegramError as exc:
                    self.stderr.write(f"getUpdates xato: {exc}")
                    if options["once"]:
                        raise CommandError(str(exc)) from None
                    time.sleep(RETRY_PAUSE_SECONDS)
                    continue

                for update in updates:
                    update_id = update["update_id"]
                    if self._process(update):
                        offset = update_id + 1
                        attempts.pop(update_id, None)
                        continue
                    # BD xatosi: update yo'qolmasligi uchun offset siljitilmaydi, biroz kutib qayta uriniladi
                    attempts[update_id] = attempts.get(update_id, 0) + 1
                    if attempts[update_id] >= MAX_ATTEMPTS_PER_UPDATE:
                        logger.error("Update %s %s urinishdan keyin o'tkazib yuborildi", update_id, attempts[update_id])
                        offset = update_id + 1
                        attempts.pop(update_id, None)
                    elif not options["once"]:
                        time.sleep(RETRY_PAUSE_SECONDS)
                    break

                self._weekly_digest()

                if options["once"]:
                    return
        except KeyboardInterrupt:
            self.stdout.write("To'xtatildi.")

    def _send_pending(self):
        """Navbatdagi eslatmalarni yuboradi; navbatda yana xabar qolgan bo'lsa True."""
        try:
            services.process_pending_notifications()
            return services.pending_count() > 0
        except DatabaseError:
            logger.exception("Navbatdagi xabarlar: ma'lumotlar bazasi xatosi")
            if not connection.in_atomic_block:
                connection.close()
        except Exception:  # bir xabarning xatosi botni to'xtatmasin
            logger.exception("Navbatdagi xabarlarni yuborishda xato")
        return False

    def _weekly_digest(self):
        try:
            digest.maybe_send_weekly_digest()
        except DatabaseError:
            logger.exception("Haftalik ro'yxat: ma'lumotlar bazasi xatosi")
            if not connection.in_atomic_block:
                connection.close()
        except Exception:  # hisob-kitob xatosi botni to'xtatmasin
            logger.exception("Haftalik ro'yxat xato")

    def _process(self, update):
        """True — update qayta ishlandi (yoki qayta ishlab bo'lmaydi); False — BD xatosi, qayta urinish kerak."""
        try:
            if "callback_query" in update:
                digest.handle_callback(update["callback_query"])
                return True
            reply = services.handle_update(update)
            if reply:
                telegram.send_message(*reply)
        except telegram.TelegramError as exc:
            self.stderr.write(f"Javob yuborilmadi: {exc}")
        except DatabaseError:
            logger.exception("Update %s: ma'lumotlar bazasi xatosi", update.get("update_id"))
            if not connection.in_atomic_block:
                connection.close()
            return False
        except Exception:  # bitta xato butun botni to'xtatmasin
            logger.exception("Update %s qayta ishlashda xato", update.get("update_id"))
        return True
