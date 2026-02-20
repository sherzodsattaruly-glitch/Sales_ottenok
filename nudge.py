"""Автоматический дожим клиентов. Простой asyncio loop."""

import asyncio
import logging
from datetime import datetime, timedelta

import db
from greenapi_client import send_text
from config import NUDGE_ENABLED, NUDGE_CHECK_INTERVAL_MINUTES

logger = logging.getLogger(__name__)

WORK_HOURS = (9, 22)  # с 9:00 до 22:00
NUDGE_DELAY_FIRST = timedelta(hours=3)

NUDGE_MESSAGES = {
    1: "Хотела уточнить, актуальна ли модель? Если есть вопросы — с радостью подскажу",
    2: "Добрый день! Вчера вы интересовались моделью из рекламы. Напомню: у нас магазин, примерка и возможность возврата — вы ничем не рискуете.",
}


def _in_work_hours(dt: datetime) -> bool:
    return WORK_HOURS[0] <= dt.hour < WORK_HOURS[1]


def _parse_ts(ts_str: str | None) -> datetime | None:
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str)
    except (ValueError, TypeError):
        return None


async def check_and_send_nudges():
    """Проверить всех кандидатов и отправить дожимы."""
    candidates = await db.get_nudge_candidates()
    now = datetime.now()

    if not _in_work_hours(now):
        return

    for c in candidates:
        chat_id = c["chat_id"]
        nudge_count = c.get("nudge_count", 0)
        last_bot_at = _parse_ts(c.get("last_bot_message_at"))

        if not last_bot_at:
            continue

        if nudge_count == 0:
            # Первый дожим: через 3 часа
            if now - last_bot_at >= NUDGE_DELAY_FIRST:
                await _send_nudge(chat_id, 1)
        elif nudge_count == 1:
            # Второй дожим: на следующий день в 13:00
            next_day_13 = (last_bot_at + timedelta(days=1)).replace(hour=13, minute=0, second=0, microsecond=0)
            if now >= next_day_13:
                await _send_nudge(chat_id, 2)


async def _send_nudge(chat_id: str, nudge_num: int):
    """Отправить дожим."""
    text = NUDGE_MESSAGES.get(nudge_num, "")
    if not text:
        return
    try:
        await send_text(chat_id, text)
        await db.save_message(chat_id, "assistant", text)
        await db.update_client(chat_id, nudge_count=nudge_num, last_nudge_at=datetime.now().isoformat())
        logger.info(f"[{chat_id}] Sent nudge #{nudge_num}")
    except Exception as e:
        logger.error(f"[{chat_id}] Failed to send nudge #{nudge_num}: {e}")


async def nudge_loop():
    """Бесконечный цикл проверки дожимов."""
    if not NUDGE_ENABLED:
        logger.info("Nudge disabled")
        return

    logger.info(f"Nudge loop started (interval: {NUDGE_CHECK_INTERVAL_MINUTES} min)")
    while True:
        try:
            await check_and_send_nudges()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Nudge loop error: {e}", exc_info=True)
        await asyncio.sleep(NUDGE_CHECK_INTERVAL_MINUTES * 60)
