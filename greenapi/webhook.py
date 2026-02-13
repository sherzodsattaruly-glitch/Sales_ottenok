"""
FastAPI роутер для приёма вебхуков от Green API.
"""

import asyncio
import logging

from fastapi import APIRouter, Request, Response

from greenapi.models import WebhookPayload
from greenapi.client import send_text
from notifications import notify_error

logger = logging.getLogger(__name__)

router = APIRouter()

# Эта функция будет заменена на AI engine после реализации
_message_handler = None

# Per-chat lock to prevent concurrent processing of messages from the same client
_chat_locks: dict[str, asyncio.Lock] = {}


def set_message_handler(handler):
    """Установить обработчик входящих сообщений (вызывается из main.py)."""
    global _message_handler
    _message_handler = handler


async def _default_echo_handler(chat_id: str, sender_name: str, text: str):
    """Эхо-обработчик для тестирования (пока нет AI engine)."""
    await send_text(chat_id, f"Вы написали: {text}")


async def process_incoming_message(chat_id: str, sender_name: str, text: str):
    """Обработка входящего сообщения в фоновой задаче."""
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


@router.post("/webhook")
async def handle_webhook(request: Request):
    """Принимает вебхуки от Green API."""
    try:
        body = await request.json()
    except Exception:
        return Response(status_code=400)

    type_webhook = body.get("typeWebhook", "")

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
        # Extract quoted message context
        quoted = message_data.extendedTextMessageData.quotedMessage
        if quoted:
            quoted_text = ""
            if "textMessage" in quoted:
                quoted_text = quoted["textMessage"]
            elif "caption" in quoted:
                quoted_text = quoted["caption"]
            elif "conversation" in quoted:
                quoted_text = quoted["conversation"]
            
            if quoted_text:
                text = f"{text} (в ответ на: \"{quoted_text}\")"
    elif message_data.typeMessage == "imageMessage" and message_data.imageMessageData:
        text = message_data.imageMessageData.caption
    elif message_data.typeMessage == "videoMessage" and message_data.videoMessageData:
        text = message_data.videoMessageData.caption

    if not text:
        return Response(status_code=200)

    chat_id = payload.senderData.chatId
    sender_name = payload.senderData.senderName or ""

    logger.info(f"[{chat_id}] Incoming message from {sender_name}: {text[:100]}")

    # Обрабатываем асинхронно (не блокируем ответ на вебхук)
    asyncio.create_task(process_incoming_message(chat_id, sender_name, text))

    return Response(status_code=200)
