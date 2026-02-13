"""
APScheduler для автоматического дожима клиентов.
Проверяет каждые 5 минут, кому нужно отправить дожим.
"""

import logging
from datetime import datetime
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from config import NUDGE_ENABLED, NUDGE_CHECK_INTERVAL_MINUTES
from .nudge_rules import should_nudge_client, get_nudge_message
from db.conversations import get_clients_for_nudge, mark_nudge_sent, get_client_order_context
from greenapi.client import send_text

logger = logging.getLogger(__name__)


class NudgeScheduler:
    """Scheduler для автоматического дожима клиентов."""

    def __init__(self):
        self.scheduler: Optional[AsyncIOScheduler] = None
        self.is_running = False

    def start(self):
        """Запустить scheduler."""
        if not NUDGE_ENABLED:
            logger.info("Nudge scheduler отключен (NUDGE_ENABLED=0)")
            return

        if self.is_running:
            logger.warning("Nudge scheduler уже запущен")
            return

        logger.info("Запуск nudge scheduler...")

        self.scheduler = AsyncIOScheduler()

        # Добавляем задачу проверки дожимов каждые N минут
        self.scheduler.add_job(
            self.check_and_send_nudges,
            trigger=IntervalTrigger(minutes=NUDGE_CHECK_INTERVAL_MINUTES),
            id="nudge_checker",
            name="Check and send nudges",
            replace_existing=True,
        )

        self.scheduler.start()
        self.is_running = True

        logger.info(
            "Nudge scheduler запущен (проверка каждые %d минут)",
            NUDGE_CHECK_INTERVAL_MINUTES
        )

    def shutdown(self):
        """Остановить scheduler."""
        if not self.is_running or not self.scheduler:
            return

        logger.info("Остановка nudge scheduler...")
        self.scheduler.shutdown(wait=True)
        self.is_running = False
        logger.info("Nudge scheduler остановлен")

    async def check_and_send_nudges(self):
        """
        Периодическая задача: проверить клиентов и отправить дожимы.
        Вызывается каждые NUDGE_CHECK_INTERVAL_MINUTES минут.
        """
        try:
            logger.debug("Проверка клиентов для дожима...")

            # Получаем список клиентов для потенциального дожима
            clients = await get_clients_for_nudge()

            if not clients:
                logger.debug("Нет клиентов для дожима")
                return

            logger.info("Найдено %d клиентов для проверки", len(clients))

            for client in clients:
                try:
                    await self._process_client_nudge(client)
                except Exception as e:
                    logger.error(
                        "Ошибка обработки дожима для chat_id=%s: %s",
                        client.get("chat_id"),
                        e,
                        exc_info=True
                    )

        except Exception as e:
            logger.error("Ошибка в check_and_send_nudges: %s", e, exc_info=True)

    async def _process_client_nudge(self, client: dict):
        """
        Обработать потенциальный дожим для одного клиента.

        Args:
            client: Словарь с данными клиента из БД
        """
        chat_id = client.get("chat_id")
        last_client_message_at = client.get("last_client_message_at")
        last_bot_message_at = client.get("last_bot_message_at")
        nudge_count = client.get("nudge_count", 0)
        handoff_enabled = client.get("handoff_enabled", False)
        last_client_text = client.get("last_client_text", "")

        # Проверяем условия для дожима
        if not should_nudge_client(
            last_client_message_at=last_client_message_at,
            last_bot_message_at=last_bot_message_at,
            nudge_count=nudge_count,
            handoff_enabled=handoff_enabled,
            last_client_text=last_client_text,
        ):
            return

        # Условия выполнены - отправляем дожим
        logger.info(
            "Отправка дожима #%d для chat_id=%s",
            nudge_count + 1,
            chat_id
        )

        # Получаем контекст заказа для персонализации (опционально)
        order_context = await get_client_order_context(chat_id)
        product_name = order_context.get("product", "") if order_context else ""

        # Формируем текст дожима
        nudge_text = get_nudge_message(nudge_count + 1, product_name)

        if not nudge_text:
            logger.warning(
                "Не удалось получить текст дожима для nudge_count=%d",
                nudge_count + 1
            )
            return

        # Отправляем дожим через GREEN-API
        try:
            await send_text(chat_id, nudge_text)
            logger.info("Дожим отправлен: chat_id=%s, nudge_count=%d", chat_id, nudge_count + 1)

            # Обновляем БД
            await mark_nudge_sent(chat_id, nudge_count + 1)

        except Exception as e:
            logger.error(
                "Ошибка отправки дожима для chat_id=%s: %s",
                chat_id,
                e,
                exc_info=True
            )


# Singleton instance
_nudge_scheduler: Optional[NudgeScheduler] = None


def get_nudge_scheduler() -> NudgeScheduler:
    """
    Получить singleton instance NudgeScheduler.

    Returns:
        Экземпляр NudgeScheduler
    """
    global _nudge_scheduler

    if _nudge_scheduler is None:
        _nudge_scheduler = NudgeScheduler()

    return _nudge_scheduler
