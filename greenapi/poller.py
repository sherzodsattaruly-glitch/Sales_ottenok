"""
Polling loop for Green API notifications (fallback when webhooks are not delivered).
"""

import asyncio
import logging

from greenapi.client import receive_notification, delete_notification, download_voice_message
from greenapi.models import WebhookPayload
from greenapi.webhook import process_incoming_message, _handle_outgoing_message
from greenapi.utils import extract_quoted_text as _extract_quoted_text

logger = logging.getLogger(__name__)


async def poll_notifications(interval: float = 2.0) -> None:
    """Poll Green API notifications and process incoming messages."""
    while True:
        try:
            notification = await receive_notification()
            if not notification:
                await asyncio.sleep(interval)
                continue

            receipt_id = notification.get("receiptId")
            body = notification.get("body") or {}

            type_webhook = body.get("typeWebhook")

            # Менеджер вручную написал в чат клиента — включаем handoff
            if type_webhook == "outgoingMessageReceived":
                import json
                logger.info(f"[poll] outgoingMessageReceived body: {json.dumps(body, ensure_ascii=False, default=str)[:500]}")
                await _handle_outgoing_message(body)
                if receipt_id is not None:
                    await delete_notification(receipt_id)
                continue

            # Сообщения отправленные через API (ботом) — игнорируем
            if type_webhook == "outgoingAPIMessageReceived":
                if receipt_id is not None:
                    await delete_notification(receipt_id)
                continue

            # Remove non-message notifications
            if type_webhook != "incomingMessageReceived":
                logger.info(f"[poll] Non-message notification: {type_webhook}")
                if receipt_id is not None:
                    await delete_notification(receipt_id)
                continue

            try:
                payload = WebhookPayload(**body)
            except Exception as e:
                logger.warning(f"Failed to parse notification payload: {e}")
                if receipt_id is not None:
                    await delete_notification(receipt_id)
                continue

            if not payload.senderData or not payload.messageData:
                if receipt_id is not None:
                    await delete_notification(receipt_id)
                continue

            message_data = payload.messageData
            text = None

            # DEBUG: log raw messageData for reply/audio messages
            raw_msg = body.get("messageData", {})
            if raw_msg.get("typeMessage") in ("extendedTextMessage", "quotedMessage", "audioMessage"):
                import json
                logger.info(f"[poll] RAW messageData: {json.dumps(raw_msg, ensure_ascii=False, default=str)[:1000]}")

            if message_data.typeMessage == "textMessage" and message_data.textMessageData:
                text = message_data.textMessageData.textMessage
            elif message_data.typeMessage == "extendedTextMessage" and message_data.extendedTextMessageData:
                text = message_data.extendedTextMessageData.text
                # Extract quoted message context
                quoted = message_data.extendedTextMessageData.quotedMessage
                if quoted:
                    logger.info(f"[poll] quotedMessage keys: {list(quoted.keys())}, values preview: {str(quoted)[:300]}")
                    quoted_text = _extract_quoted_text(quoted)
                    if quoted_text:
                        text = f"{text} (в ответ на: \"{quoted_text}\")"
                    else:
                        logger.warning(f"[poll] quotedMessage present but extract_quoted_text returned empty")

            elif message_data.typeMessage == "quotedMessage":
                # Reply на сообщение — текст может быть в quotedMessageData или в raw body
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
                                # Пользователь не написал текст, только reply — используем caption фото
                                text = f"(в ответ на: \"{quoted_text}\")"

                # Fallback: ищем текст в raw body
                if not text:
                    raw_msg = body.get("messageData", {})
                    # extendedTextMessageData может присутствовать даже при typeMessage=quotedMessage
                    ext = raw_msg.get("extendedTextMessageData") or {}
                    raw_text = ext.get("text", "")
                    if raw_text:
                        text = raw_text
                        # quotedMessage лежит на уровне messageData (рядом с extendedTextMessageData)
                        quoted = raw_msg.get("quotedMessage") or ext.get("quotedMessage")
                        if quoted:
                            quoted_text = _extract_quoted_text(quoted)
                            if quoted_text:
                                text = f"{text} (в ответ на: \"{quoted_text}\")"

                if not text:
                    logger.info(
                        f"[poll] quotedMessage with no extractable text, raw messageData keys: "
                        f"{list(body.get('messageData', {}).keys())}"
                    )

            elif message_data.typeMessage == "imageMessage" and message_data.imageMessageData:
                text = message_data.imageMessageData.caption
            elif message_data.typeMessage == "videoMessage" and message_data.videoMessageData:
                text = message_data.videoMessageData.caption
            elif message_data.typeMessage == "audioMessage":
                # Голосовое сообщение — берём downloadUrl из raw body (Pydantic может не парсить)
                chat_id = payload.senderData.chatId
                sender_name = payload.senderData.senderName or ""
                raw_msg = body.get("messageData", {})
                # downloadUrl может быть в fileMessageData или audioMessageData
                file_data = raw_msg.get("fileMessageData") or raw_msg.get("audioMessageData") or {}
                download_url = file_data.get("downloadUrl", "")
                mime_type = file_data.get("mimeType", "audio/ogg")
                import json
                logger.info(f"[poll] audioMessage raw keys: {list(raw_msg.keys())}, file_data keys: {list(file_data.keys())}")
                if download_url:
                    logger.info(f"[poll] Voice message from {sender_name} ({chat_id}), transcribing...")
                    try:
                        from ai.engine import transcribe_voice
                        audio_bytes = await download_voice_message(download_url)
                        if audio_bytes:
                            transcribed = await transcribe_voice(audio_bytes, mime_type)
                            if transcribed:
                                logger.info(f"[poll] Voice transcribed: {transcribed[:100]}")
                                await process_incoming_message(chat_id, sender_name, transcribed)
                            else:
                                logger.info(f"[poll] Whisper returned empty transcription")
                        else:
                            logger.warning(f"[poll] Empty audio download from {download_url}")
                    except Exception as e:
                        logger.error(f"[poll] Voice processing failed: {e}", exc_info=True)
                else:
                    logger.warning(f"[poll] audioMessage without downloadUrl, raw: {json.dumps(raw_msg, ensure_ascii=False, default=str)[:500]}")
                if receipt_id is not None:
                    await delete_notification(receipt_id)
                continue

            if text:
                chat_id = payload.senderData.chatId
                sender_name = payload.senderData.senderName or ""
                logger.info(f"[poll] Incoming message from {sender_name} ({chat_id}): {text[:100]}")
                await process_incoming_message(chat_id, sender_name, text)
            else:
                logger.info(f"[poll] Skipped message of type {message_data.typeMessage} (no text/caption)")

            if receipt_id is not None:
                await delete_notification(receipt_id)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Polling error: {e}", exc_info=True)
            await asyncio.sleep(interval)
