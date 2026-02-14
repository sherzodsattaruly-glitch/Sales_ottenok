"""
Polling loop for Green API notifications (fallback when webhooks are not delivered).
"""

import asyncio
import logging

from greenapi.client import receive_notification, delete_notification
from greenapi.models import WebhookPayload
from greenapi.webhook import process_incoming_message

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

            # Remove non-message notifications
            if body.get("typeWebhook") != "incomingMessageReceived":
                logger.debug(f"Received non-message notification: {body.get('typeWebhook')}")
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

            if message_data.typeMessage == "textMessage" and message_data.textMessageData:
                text = message_data.textMessageData.textMessage
            elif message_data.typeMessage == "extendedTextMessage" and message_data.extendedTextMessageData:
                text = message_data.extendedTextMessageData.text
                # Extract quoted message context
                quoted = message_data.extendedTextMessageData.quotedMessage
                if quoted:
                    quoted_text = ""
                    # Check different possible fields in quoted message
                    if "textMessage" in quoted:
                        quoted_text = quoted["textMessage"]
                    elif "caption" in quoted:
                        quoted_text = quoted["caption"]
                    elif "conversation" in quoted:
                        quoted_text = quoted["conversation"]

                    if quoted_text:
                        text = f"{text} (в ответ на: \"{quoted_text}\")"

            elif message_data.typeMessage == "quotedMessage" and message_data.quotedMessageData:
                text = message_data.quotedMessageData.text
                # Extract quoted message context
                quoted = message_data.quotedMessageData.quotedMessage
                if quoted:
                    quoted_text = ""
                    # Check different possible fields in quoted message
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
