"""Автоматический дожим клиентов. Простой asyncio loop + LLM-валидация."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from openai import AsyncOpenAI
from agents import SQLiteSession

import db
from greenapi_client import send_text
from config import NUDGE_ENABLED, NUDGE_CHECK_INTERVAL_MINUTES, AGENT_SESSIONS_DB_PATH, OPENAI_API_KEY

logger = logging.getLogger(__name__)

WORK_HOURS = (10, 22)  # с 10:00 до 22:00
NUDGE_DELAY_FIRST = timedelta(hours=1)

NUDGE_MESSAGES = {
    1: "Хотела уточнить, актуальна ли модель? Если есть вопросы — с радостью подскажу",
    2: "Добрый день! Вчера вы интересовались моделью из рекламы. Напомню: у нас магазин, примерка и возможность возврата — вы ничем не рискуете.",
}

CLASSIFIER_PROMPT = """Определи статус диалога по последним сообщениям. Ответь ОДНИМ словом:
- genuine_silence — клиент интересовался товаром но замолчал, уместно напомнить
- ordered — клиент оформил/оплатил заказ
- fitting — клиент сказал что приедет на примерку или в магазин
- declined — клиент отказался или сказал "не надо", "не интересно"
- resolved — вопрос клиента решён (узнал адрес, цену и т.д.), дожим не нужен

Диалог:
{conversation}

Статус:"""

_openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)


def _in_work_hours(dt: datetime) -> bool:
    return WORK_HOURS[0] <= dt.hour < WORK_HOURS[1]


def _parse_ts(ts_str: str | None) -> datetime | None:
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str)
    except (ValueError, TypeError):
        return None


async def _should_nudge(chat_id: str) -> bool:
    """LLM-классификатор: проверяет нужен ли дожим по контексту диалога."""
    try:
        messages = await db.get_recent_messages(chat_id, limit=6)
        if not messages:
            return True  # нет истории — дожимаем по умолчанию

        conversation = "\n".join(
            f"{'Клиент' if m['role'] == 'user' else 'Алина'}: {m['content'][:200]}"
            for m in messages
        )

        response = await _openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": CLASSIFIER_PROMPT.format(conversation=conversation)}],
            max_tokens=10,
            temperature=0,
        )
        status = response.choices[0].message.content.strip().lower()
        logger.info(f"[{chat_id}] Nudge classifier: {status}")

        if status == "genuine_silence":
            return True

        # Классификатор определил что дожим не нужен — обновляем статус в БД
        status_map = {"ordered": "ordered", "fitting": "fitting", "declined": "declined"}
        if status in status_map:
            await db.set_client_status(chat_id, status_map[status])
            logger.info(f"[{chat_id}] Auto-set client_status={status} via classifier")

        return False
    except Exception as e:
        logger.error(f"[{chat_id}] Nudge classifier error: {e}")
        return False  # при ошибке лучше НЕ дожимать


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
            # Первый дожим: через 1 час (отправится только в рабочее время 10-22)
            if now - last_bot_at >= NUDGE_DELAY_FIRST:
                if await _should_nudge(chat_id):
                    await _send_nudge(chat_id, 1)
        elif nudge_count == 1:
            # Второй дожим: на следующий день в 13:00 от момента 1-го дожима
            nudge1_at = _parse_ts(c.get("last_nudge_at")) or last_bot_at
            next_day_13 = (nudge1_at + timedelta(days=1)).replace(hour=13, minute=0, second=0, microsecond=0)
            if now >= next_day_13:
                if await _should_nudge(chat_id):
                    await _send_nudge(chat_id, 2)


async def _send_nudge(chat_id: str, nudge_num: int):
    """Отправить дожим."""
    text = NUDGE_MESSAGES.get(nudge_num, "")
    if not text:
        return
    try:
        await send_text(chat_id, text)
        await db.save_message(chat_id, "assistant", text)
        # Сохраняем дожим в agent session, чтобы LLM видел его в истории
        session = SQLiteSession(session_id=chat_id, db_path=AGENT_SESSIONS_DB_PATH)
        await session.add_items([{"role": "assistant", "content": text}])
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
