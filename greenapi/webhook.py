"""
FastAPI роутер для приёма вебхуков от Green API.
"""

import asyncio
import logging

from fastapi import APIRouter, Request, Response

from greenapi.models import WebhookPayload
from greenapi.client import send_text, download_voice_message
from greenapi.utils import extract_quoted_text as _extract_quoted_text
from notifications import notify_error
from config import MANAGER_CHAT_IDS, MESSAGE_AGGREGATION_DELAY, GREEN_API_POLLING
from db.conversations import set_handoff_state

logger = logging.getLogger(__name__)

router = APIRouter()

# Эта функция будет заменена на AI engine после реализации
_message_handler = None

# Per-chat lock to prevent concurrent processing of messages from the same client
_chat_locks: dict[str, asyncio.Lock] = {}

# Message aggregation (debounce) state
_message_buffers: dict[str, list[str]] = {}       # chat_id -> [text1, text2, ...]
_buffer_sender: dict[str, str] = {}               # chat_id -> sender_name (from first msg)
_buffer_timers: dict[str, asyncio.Task] = {}       # chat_id -> pending flush timer task


def set_message_handler(handler):
    """Установить обработчик входящих сообщений (вызывается из main.py)."""
    global _message_handler
    _message_handler = handler


async def _default_echo_handler(chat_id: str, sender_name: str, text: str):
    """Эхо-обработчик для тестирования (пока нет AI engine)."""
    await send_text(chat_id, f"Вы написали: {text}")


def _is_manager_command(chat_id: str, text: str) -> bool:
    """Check if message is a manager command that should bypass buffering."""
    if chat_id not in MANAGER_CHAT_IDS:
        return False
    t = text.strip().lower()
    return t.startswith("/handoff") or t.startswith("/bot ")


async def process_incoming_message(chat_id: str, sender_name: str, text: str):
    """Buffer incoming messages per chat_id; flush after AGGREGATION_DELAY seconds of silence."""

    # Manager commands bypass buffering entirely
    if _is_manager_command(chat_id, text):
        await _execute_handler(chat_id, sender_name, text)
        return

    # If aggregation is disabled, process immediately
    if MESSAGE_AGGREGATION_DELAY <= 0:
        await _execute_handler(chat_id, sender_name, text)
        return

    # Add message to buffer
    if chat_id not in _message_buffers:
        _message_buffers[chat_id] = []
        _buffer_sender[chat_id] = sender_name
    _message_buffers[chat_id].append(text)

    logger.debug(
        f"[{chat_id}] Buffered message ({len(_message_buffers[chat_id])} in queue): {text[:80]}"
    )

    # Cancel existing timer for this chat
    if chat_id in _buffer_timers:
        _buffer_timers[chat_id].cancel()

    # Start new timer
    _buffer_timers[chat_id] = asyncio.create_task(_flush_after_delay(chat_id))


async def _flush_after_delay(chat_id: str):
    """Wait for AGGREGATION_DELAY then flush the buffer."""
    try:
        await asyncio.sleep(MESSAGE_AGGREGATION_DELAY)
        await _flush_buffer(chat_id)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"[{chat_id}] Error in flush timer: {e}", exc_info=True)


async def _flush_buffer(chat_id: str):
    """Combine all buffered messages and process as one."""
    messages = _message_buffers.pop(chat_id, [])
    sender = _buffer_sender.pop(chat_id, "")
    _buffer_timers.pop(chat_id, None)

    if not messages:
        return

    combined_text = "\n".join(messages)

    if len(messages) > 1:
        logger.info(
            f"[{chat_id}] Aggregated {len(messages)} messages into one: {combined_text[:120]}"
        )

    await _execute_handler(chat_id, sender, combined_text)


async def _execute_handler(chat_id: str, sender_name: str, text: str):
    """Acquire per-chat lock and execute the message handler."""
    if chat_id not in _chat_locks:
        _chat_locks[chat_id] = asyncio.Lock()
    async with _chat_locks[chat_id]:
        handler = _message_handler or _default_echo_handler
        try:
            await handler(chat_id, sender_name, text)
        except Exception as e:
            logger.error(f"[{chat_id}] Error processing message: {e}", exc_info=True)
            await notify_error("webhook", f"chat_id={chat_id} error={e}")
            try:
                await send_text(chat_id, "Извините, произошла ошибка. Наш менеджер скоро с вами свяжется!")
            except Exception:
                logger.error(f"[{chat_id}] Failed to send error message", exc_info=True)


async def _handle_outgoing_message(body: dict) -> None:
    """Detect manager manually typing in client chat and auto-enable handoff.

    Reaction (emoji) on any message in client chat → bot resumes responding.
    Regular text message → manager takeover, bot stops responding.
    """
    try:
        chat_id = body.get("senderData", {}).get("chatId", "")
        if not chat_id:
            return
        if "@g.us" in chat_id:
            return

        msg_data = body.get("messageData", {})
        type_message = msg_data.get("typeMessage", "")

        # Reaction emoji → turn bot back on for this chat
        if type_message == "reactionMessage":
            await set_handoff_state(chat_id, False)
            logger.info(f"[{chat_id}] Reaction detected — handoff DISABLED, bot will respond again")
            return

        # Extract message text
        msg_text = ""
        if type_message == "textMessage":
            msg_text = (msg_data.get("textMessageData") or {}).get("textMessage", "")
        elif type_message == "extendedTextMessage":
            msg_text = (msg_data.get("extendedTextMessageData") or {}).get("text", "")

        cmd = msg_text.strip().lower()

        # /bot on — turn bot back on for this chat (fallback command)
        if cmd == "/bot on":
            await set_handoff_state(chat_id, False)
            logger.info(f"[{chat_id}] /bot on command — handoff DISABLED, bot will respond again")
            return

        # /bot off — turn bot off for this chat
        if cmd == "/bot off":
            await set_handoff_state(chat_id, True)
            logger.info(f"[{chat_id}] /bot off command — handoff ENABLED, bot stopped")
            return

        # Regular outgoing message — enable handoff (manager takeover)
        if chat_id in MANAGER_CHAT_IDS:
            return
        await set_handoff_state(chat_id, True)
        logger.info(f"[{chat_id}] Outgoing message detected (manager takeover), enabling handoff")
    except Exception as e:
        logger.error(f"Error handling outgoing message: {e}", exc_info=True)


async def _process_voice_message(
    chat_id: str, sender_name: str, download_url: str, mime_type: str
) -> None:
    """Скачать голосовое, транскрибировать через Whisper, обработать как текст."""
    from ai.engine import transcribe_voice

    try:
        audio_bytes = await download_voice_message(download_url)
        if not audio_bytes:
            logger.warning(f"[{chat_id}] Empty audio download")
            return

        text = await transcribe_voice(audio_bytes, mime_type)
        if not text:
            logger.info(f"[{chat_id}] Whisper returned empty transcription")
            return

        logger.info(f"[{chat_id}] Voice transcribed: {text[:100]}")
        await process_incoming_message(chat_id, sender_name, text)
    except Exception as e:
        logger.error(f"[{chat_id}] Voice message processing failed: {e}", exc_info=True)


@router.post("/webhook")
async def handle_webhook(request: Request):
    """Принимает вебхуки от Green API."""
    # Когда polling активен, все уведомления обрабатываются через poller —
    # webhook не должен дублировать обработку
    if GREEN_API_POLLING:
        return Response(status_code=200)

    try:
        body = await request.json()
    except Exception:
        return Response(status_code=400)

    type_webhook = body.get("typeWebhook", "")

    # Менеджер вручную написал в чат клиента — включаем handoff
    if type_webhook == "outgoingMessageReceived":
        asyncio.create_task(_handle_outgoing_message(body))
        return Response(status_code=200)

    # Обрабатываем только входящие сообщения
    if type_webhook != "incomingMessageReceived":
        return Response(status_code=200)

    try:
        payload = WebhookPayload(**body)
    except Exception as e:
        logger.warning(f"Failed to parse webhook: {e}")
        return Response(status_code=200)

    if not payload.senderData or not payload.messageData:
        return Response(status_code=200)

    # Извлекаем текст сообщения
    message_data = payload.messageData
    text = None

    if message_data.typeMessage == "textMessage" and message_data.textMessageData:
        text = message_data.textMessageData.textMessage
    elif message_data.typeMessage == "extendedTextMessage" and message_data.extendedTextMessageData:
        text = message_data.extendedTextMessageData.text
        quoted = message_data.extendedTextMessageData.quotedMessage
        if quoted:
            quoted_text = _extract_quoted_text(quoted)
            if quoted_text:
                text = f"{text} (в ответ на: \"{quoted_text}\")"
    elif message_data.typeMessage == "quotedMessage":
        text = None
        if message_data.quotedMessageData:
            text = message_data.quotedMessageData.text
            quoted = message_data.quotedMessageData.quotedMessage
            if quoted:
                quoted_text = _extract_quoted_text(quoted)
                if quoted_text:
                    if text:
                        text = f"{text} (в ответ на: \"{quoted_text}\")"
                    else:
                        text = f"(в ответ на: \"{quoted_text}\")"
    elif message_data.typeMessage == "imageMessage" and message_data.imageMessageData:
        text = message_data.imageMessageData.caption
    elif message_data.typeMessage == "videoMessage" and message_data.videoMessageData:
        text = message_data.videoMessageData.caption
    elif message_data.typeMessage == "audioMessage" and message_data.fileMessageData:
        # Голосовое сообщение — скачиваем и транскрибируем
        chat_id = payload.senderData.chatId
        sender_name = payload.senderData.senderName or ""
        download_url = message_data.fileMessageData.downloadUrl
        mime_type = message_data.fileMessageData.mimeType or "audio/ogg"
        if download_url:
            logger.info(f"[{chat_id}] Voice message from {sender_name}, downloading...")
            asyncio.create_task(
                _process_voice_message(chat_id, sender_name, download_url, mime_type)
            )
        return Response(status_code=200)

    if not text:
        return Response(status_code=200)

    chat_id = payload.senderData.chatId
    sender_name = payload.senderData.senderName or ""

    logger.info(f"[{chat_id}] Incoming message from {sender_name}: {text[:100]}")

    # Обрабатываем асинхронно (не блокируем ответ на вебхук)
    asyncio.create_task(process_incoming_message(chat_id, sender_name, text))

    return Response(status_code=200)
