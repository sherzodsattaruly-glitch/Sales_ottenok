"""Chat manager — message routing, aggregation, per-chat locks, handoff."""

import asyncio
import logging

import aiosqlite

from config import MANAGER_CHAT_IDS, MESSAGE_AGGREGATION_DELAY, AGENT_SESSIONS_DB_PATH, SQLITE_DB_PATH
from greenapi_client import send_text
from services import notify_error
import db
import ai

logger = logging.getLogger(__name__)

_chat_locks: dict[str, asyncio.Lock] = {}  # TODO: WeakValueDictionary не работает с Lock (нет weak ref), используем обычный dict
_message_buffers: dict[str, list[str]] = {}
_buffer_sender: dict[str, str] = {}
_buffer_timers: dict[str, asyncio.Task] = {}


def _is_manager_command(chat_id: str, text: str) -> bool:
    if chat_id not in MANAGER_CHAT_IDS:
        return False
    t = text.strip().lower()
    return t.startswith("/handoff") or t.startswith("/bot ") or t.startswith("/reset")


async def handle_message(chat_id: str, sender_name: str, text: str, image_data: bytes | None = None):
    """Входная точка для всех сообщений."""

    # Manager commands bypass everything
    if _is_manager_command(chat_id, text):
        await _handle_manager_command(chat_id, text)
        return

    # Фото — обрабатываем сразу, без агрегации (изображение теряет контекст при объединении)
    if image_data:
        await _process(chat_id, sender_name, text, image_data=image_data)
        return

    # Aggregation
    if MESSAGE_AGGREGATION_DELAY > 0:
        if chat_id not in _message_buffers:
            _message_buffers[chat_id] = []
            _buffer_sender[chat_id] = sender_name
        _message_buffers[chat_id].append(text)

        if chat_id in _buffer_timers:
            _buffer_timers[chat_id].cancel()
        _buffer_timers[chat_id] = asyncio.create_task(_flush_after_delay(chat_id))
    else:
        await _process(chat_id, sender_name, text)


async def _flush_after_delay(chat_id: str):
    try:
        await asyncio.sleep(MESSAGE_AGGREGATION_DELAY)
        messages = _message_buffers.pop(chat_id, [])
        sender = _buffer_sender.pop(chat_id, "")
        _buffer_timers.pop(chat_id, None)
        if messages:
            combined = "\n".join(messages)
            if len(messages) > 1:
                logger.info(f"[{chat_id}] Aggregated {len(messages)} messages")
            await _process(chat_id, sender, combined)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"[{chat_id}] Flush error: {e}", exc_info=True)


async def _process(chat_id: str, sender_name: str, text: str, image_data: bytes | None = None):
    """Process message with per-chat lock."""
    if chat_id not in _chat_locks:
        _chat_locks[chat_id] = asyncio.Lock()

    async with _chat_locks[chat_id]:
        try:
            # Check handoff
            if await db.is_handoff(chat_id):
                # Save message but don't respond
                await db.save_message(chat_id, "user", text, sender_name)
                logger.info(f"[{chat_id}] Handoff active, skipping AI response")
                return

            # Reset nudge count on client message
            await db.update_client(chat_id, nudge_count=0)

            # Generate AI response
            response = await ai.generate_response(chat_id, text, sender_name, image_data=image_data)

            if not response:
                return

            # Split by ||| and send as separate messages
            parts = [p.strip() for p in response.split("|||") if p.strip()]
            for part in parts:
                await send_text(chat_id, part)
                if len(parts) > 1:
                    await asyncio.sleep(0.5)

        except Exception as e:
            logger.error(f"[{chat_id}] Error: {e}", exc_info=True)
            await notify_error("chat", f"chat_id={chat_id} error={e}")
            try:
                await send_text(chat_id, "Извините, произошла ошибка. Наш менеджер скоро с вами свяжется!")
            except Exception:
                pass


async def _handle_manager_command(chat_id: str, text: str):
    """Обработка команд менеджера."""
    parts = text.strip().split()
    cmd = parts[0].lower()

    if cmd == "/handoff" and len(parts) >= 3:
        action = parts[1].lower()
        target = parts[2]
        if "@c.us" not in target:
            target = f"{target}@c.us"

        if action == "on":
            await db.set_handoff(target, True)
            await send_text(chat_id, f"✅ Handoff включен для {target}")
        elif action == "off":
            await db.set_handoff(target, False)
            await send_text(chat_id, f"✅ Handoff выключен для {target}")
        elif action == "status":
            is_on = await db.is_handoff(target)
            await send_text(chat_id, f"Handoff для {target}: {'включен' if is_on else 'выключен'}")

    elif cmd == "/bot" and len(parts) >= 2:
        # /bot on/off — для текущего чата или указанного
        action = parts[1].lower()
        target = parts[2] if len(parts) > 2 else chat_id
        if "@c.us" not in target:
            target = f"{target}@c.us"

        if action == "on":
            await db.set_handoff(target, False)
            await send_text(chat_id, f"✅ Бот включен для {target}")
        elif action == "off":
            await db.set_handoff(target, True)
            await send_text(chat_id, f"✅ Бот выключен для {target}")

    elif cmd == "/reset":
        # /reset <phone> — очистить сессию (LLM-контекст + sent_photos)
        # /reset all — очистить все сессии
        target = parts[1] if len(parts) >= 2 else ""

        if target.lower() == "all":
            async with aiosqlite.connect(AGENT_SESSIONS_DB_PATH) as sdb:
                await sdb.execute("DELETE FROM agent_messages")
                await sdb.execute("DELETE FROM agent_sessions")
                await sdb.commit()
            async with aiosqlite.connect(SQLITE_DB_PATH) as sdb:
                await sdb.execute("DELETE FROM sent_photos")
                await sdb.commit()
            await send_text(chat_id, "✅ Все сессии очищены")
            logger.info("Manager reset ALL sessions")

        elif target:
            if "@c.us" not in target:
                target = f"{target}@c.us"
            async with aiosqlite.connect(AGENT_SESSIONS_DB_PATH) as sdb:
                await sdb.execute("DELETE FROM agent_messages WHERE session_id = ?", (target,))
                await sdb.execute("DELETE FROM agent_sessions WHERE session_id = ?", (target,))
                await sdb.commit()
            async with aiosqlite.connect(SQLITE_DB_PATH) as sdb:
                await sdb.execute("DELETE FROM sent_photos WHERE chat_id = ?", (target,))
                await sdb.commit()
            await send_text(chat_id, f"✅ Сессия очищена для {target}")
            logger.info(f"Manager reset session for {target}")

        else:
            await send_text(chat_id, "Формат: /reset <номер> или /reset all")


async def handle_outgoing_message(body: dict):
    """Менеджер написал в чат клиента — auto handoff."""
    try:
        chat_id = body.get("senderData", {}).get("chatId", "")
        if not chat_id or "@g.us" in chat_id:
            return

        msg_data = body.get("messageData", {})
        type_message = msg_data.get("typeMessage", "")

        # Reaction → turn bot back on
        if type_message == "reactionMessage":
            await db.set_handoff(chat_id, False)
            logger.info(f"[{chat_id}] Reaction → handoff OFF")
            return

        # Extract text for /bot commands
        msg_text = ""
        if type_message == "textMessage":
            msg_text = (msg_data.get("textMessageData") or {}).get("textMessage", "")
        elif type_message == "extendedTextMessage":
            msg_text = (msg_data.get("extendedTextMessageData") or {}).get("text", "")

        cmd = msg_text.strip().lower()
        if cmd == "/bot on":
            await db.set_handoff(chat_id, False)
            return
        if cmd == "/bot off":
            await db.set_handoff(chat_id, True)
            return

        # Regular message → handoff
        if chat_id not in MANAGER_CHAT_IDS:
            await db.set_handoff(chat_id, True)
            logger.info(f"[{chat_id}] Manager takeover → handoff ON")
    except Exception as e:
        logger.error(f"Outgoing handler error: {e}", exc_info=True)
