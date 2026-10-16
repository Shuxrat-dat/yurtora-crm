"""BOT-01: бот переживает сбои БД — обновляет соединение, повторяет update, не теряет и не зацикливается."""

import pytest
from django.core.management import call_command
from django.db import OperationalError

from apps.notifications import digest, routing, services, telegram
from apps.notifications.management.commands import run_telegram_bot as bot

pytestmark = pytest.mark.django_db

UPDATE = {"update_id": 10, "message": {"text": "/start", "chat": {"id": 1, "type": "private"}}}


class Harness:
    """Подменяет Telegram и считает вызовы; get_updates отдаёт заготовленный сценарий, затем Ctrl+C."""

    def __init__(self, monkeypatch, scenario, handle_update):
        self.offsets, self.sent, self.refreshes, self.sleeps = [], [], 0, []
        script = iter(scenario)

        def get_updates(offset=None, timeout=30):
            self.offsets.append(offset)
            try:
                step = next(script)
            except StopIteration:
                raise KeyboardInterrupt from None
            return step

        def refresh():
            self.refreshes += 1

        monkeypatch.setattr(telegram, "is_configured", lambda: True)
        monkeypatch.setattr(telegram, "get_updates", get_updates)
        monkeypatch.setattr(telegram, "send_message", lambda chat_id, text, **kw: self.sent.append((chat_id, text)))
        monkeypatch.setattr(routing, "handle_update", handle_update)
        monkeypatch.setattr(digest, "maybe_send_weekly_digest", lambda: None)
        monkeypatch.setattr(bot, "refresh_db_connection", refresh)
        monkeypatch.setattr(bot.time, "sleep", lambda s: self.sleeps.append(s))

    def run(self):
        call_command("run_telegram_bot")


def test_db_error_is_retried_without_losing_the_update(monkeypatch):
    calls = []

    def handle_update(update):
        calls.append(update["update_id"])
        if len(calls) == 1:
            raise OperationalError("server closed the connection unexpectedly")
        return 1, "ok"

    h = Harness(monkeypatch, [[UPDATE], [UPDATE]], handle_update)
    h.run()
    assert calls == [10, 10]  # тот же update обработан повторно
    assert h.offsets == [None, None, 11]  # offset сдвинут только после успеха
    assert h.sent == [(1, "ok")]
    assert h.sleeps == [bot.RETRY_PAUSE_SECONDS]


def test_persistent_db_error_skips_update_after_max_attempts(monkeypatch):
    def handle_update(update):
        raise OperationalError("db is down")

    h = Harness(monkeypatch, [[UPDATE], [UPDATE], [UPDATE]], handle_update)
    h.run()
    assert h.offsets == [None, None, None, 11]  # после 3 попыток update пропущен, бот не зациклился
    assert h.sent == []


def test_unexpected_error_does_not_block_the_queue(monkeypatch):
    def handle_update(update):
        raise ValueError("bug in handler")

    h = Harness(monkeypatch, [[UPDATE]], handle_update)
    h.run()
    assert h.offsets == [None, 11]


def test_connection_is_refreshed_on_every_loop(monkeypatch):
    h = Harness(monkeypatch, [[], [], []], lambda update: None)
    h.run()
    assert h.refreshes == 4  # по разу на каждый get_updates, включая последний (Ctrl+C)


def test_refresh_closes_stale_connection_outside_transactions(monkeypatch):
    closed = []
    monkeypatch.setattr(bot, "close_old_connections", lambda: closed.append(True))
    monkeypatch.setattr(bot.connection, "in_atomic_block", False, raising=False)
    bot.refresh_db_connection()
    assert closed == [True]
    monkeypatch.setattr(bot.connection, "in_atomic_block", True, raising=False)
    bot.refresh_db_connection()
    assert closed == [True]  # внутри транзакции (тесты) не трогаем


def test_missing_token_fails_fast(monkeypatch):
    monkeypatch.setattr(telegram, "is_configured", lambda: False)
    with pytest.raises(Exception, match="TELEGRAM_BOT_TOKEN"):
        call_command("run_telegram_bot")


def test_loop_sends_the_queue_and_polls_briefly_while_backlog_remains(monkeypatch):
    """BOT-02: цикл бота обрабатывает очередь рассылки; пока очередь не пуста, не засыпает на 30 секунд."""
    processed, timeouts = [], []
    state = {"pending": 2}

    def process_pending(limit=25, pause=0.05):
        processed.append(True)
        state["pending"] = max(0, state["pending"] - 1)

    h = Harness(monkeypatch, [[], []], lambda update: None)
    original_get_updates = telegram.get_updates

    def spy(offset=None, timeout=30):
        timeouts.append(timeout)
        return original_get_updates(offset=offset, timeout=timeout)

    monkeypatch.setattr(telegram, "get_updates", spy)
    monkeypatch.setattr(services, "process_pending_notifications", process_pending)
    monkeypatch.setattr(services, "pending_count", lambda: state["pending"])
    h.run()
    assert len(processed) >= 2
    assert timeouts[0] == 2 and timeouts[-1] in (2, 30)
    assert timeouts[0] < 30


def test_queue_error_does_not_stop_the_bot(monkeypatch):
    def boom(limit=25, pause=0.05):
        raise OperationalError("db gone")

    h = Harness(monkeypatch, [[]], lambda update: None)
    monkeypatch.setattr(services, "process_pending_notifications", boom)
    h.run()  # завершилось по Ctrl+C сценария, а не упало
    assert h.offsets
