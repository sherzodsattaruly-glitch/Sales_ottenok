"""Sales Ottenok v2 — точка входа. FastAPI + polling + nudge."""

import logging
from logging.handlers import RotatingFileHandler
import asyncio
import time as _time
from pathlib import Path
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request, Response
from pydantic import BaseModel
from typing import Optional

from config import WEBHOOK_HOST, WEBHOOK_PORT, GREEN_API_POLLING, GREEN_API_POLL_INTERVAL
from db import init_db
from chat import handle_message, handle_outgoing_message
from nudge import nudge_loop
from services import load_photo_index

# ── Logging ──────────────────────────────────────────────────

Path("data").mkdir(exist_ok=True)

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler("data/bot.log", maxBytes=10*1024*1024, backupCount=5, encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


# ── Pydantic models ──────────────────────────────────────────

class SenderData(BaseModel):
    chatId: str
    sender: str = ""
    senderName: Optional[str] = ""

class TextMessageData(BaseModel):
    textMessage: str = ""

class ExtendedTextMessageData(BaseModel):
    text: str = ""
    quotedMessage: Optional[dict] = None

class CaptionData(BaseModel):
    caption: Optional[str] = ""

class FileData(BaseModel):
    downloadUrl: Optional[str] = None
    mimeType: Optional[str] = None

class MessageData(BaseModel):
    typeMessage: str
    textMessageData: Optional[TextMessageData] = None
    extendedTextMessageData: Optional[ExtendedTextMessageData] = None
    quotedMessageData: Optional[ExtendedTextMessageData] = None
    imageMessageData: Optional[CaptionData] = None
    videoMessageData: Optional[CaptionData] = None
    fileMessageData: Optional[FileData] = None

class WebhookPayload(BaseModel):
    typeWebhook: str
    senderData: Optional[SenderData] = None
    messageData: Optional[MessageData] = None


# ── Text extraction ──────────────────────────────────────────

def _extract_quoted_text(quoted: dict) -> str:
    for key in ("caption", "textMessage", "conversation"):
        if quoted.get(key):
            return quoted[key]
    return ""


def extract_text(payload: WebhookPayload, raw_body: dict) -> str | None:
    """Extract text from incoming message payload."""
    md = payload.messageData
    if not md:
        return None

    if md.typeMessage == "textMessage" and md.textMessageData:
        return md.textMessageData.textMessage

    if md.typeMessage == "extendedTextMessage" and md.extendedTextMessageData:
        text = md.extendedTextMessageData.text
        q = md.extendedTextMessageData.quotedMessage
        if q:
            qt = _extract_quoted_text(q)
            if qt:
                text = f'{text} (в ответ на: "{qt}")'
        return text

    if md.typeMessage == "quotedMessage":
        # Try Pydantic model
        if md.quotedMessageData and md.quotedMessageData.text:
            text = md.quotedMessageData.text
            q = md.quotedMessageData.quotedMessage
            if q:
                qt = _extract_quoted_text(q)
                if qt:
                    text = f'{text} (в ответ на: "{qt}")'
            return text
        # Fallback: raw body
        raw_md = raw_body.get("messageData", {})
        ext = raw_md.get("extendedTextMessageData") or {}
        if ext.get("text"):
            text = ext["text"]
            q = raw_md.get("quotedMessage") or ext.get("quotedMessage")
            if q:
                qt = _extract_quoted_text(q)
                if qt:
                    text = f'{text} (в ответ на: "{qt}")'
            return text
        return None

    if md.typeMessage == "imageMessage" and md.imageMessageData:
        return md.imageMessageData.caption or None

    if md.typeMessage == "videoMessage" and md.videoMessageData:
        return md.videoMessageData.caption or None

    return None


def is_voice_message(payload: WebhookPayload, raw_body: dict) -> tuple[str, str] | None:
    """Return (download_url, mime_type) if voice message, else None."""
    md = payload.messageData
    if not md or md.typeMessage != "audioMessage":
        return None
    raw_md = raw_body.get("messageData", {})
    fd = raw_md.get("fileMessageData") or raw_md.get("audioMessageData") or {}
    url = fd.get("downloadUrl", "")
    mime = fd.get("mimeType", "audio/ogg")
    return (url, mime) if url else None


# ── Polling ──────────────────────────────────────────────────

async def poll_loop(interval: float):
    from greenapi_client import receive_notification, delete_notification, download_file

    logger.info(f"Polling started (interval={interval}s)")
    while True:
        try:
            notification = await receive_notification()
            if not notification:
                await asyncio.sleep(interval)
                continue

            receipt_id = notification.get("receiptId")
            body = notification.get("body") or {}
            type_wh = body.get("typeWebhook", "")

            if type_wh == "outgoingMessageReceived":
                asyncio.create_task(handle_outgoing_message(body))
                if receipt_id is not None:
                    await delete_notification(receipt_id)
                continue

            if type_wh != "incomingMessageReceived":
                if receipt_id is not None:
                    await delete_notification(receipt_id)
                continue

            try:
                payload = WebhookPayload(**body)
            except Exception:
                if receipt_id is not None:
                    await delete_notification(receipt_id)
                continue

            if not payload.senderData or not payload.messageData:
                if receipt_id is not None:
                    await delete_notification(receipt_id)
                continue

            chat_id = payload.senderData.chatId
            sender_name = payload.senderData.senderName or ""

            # Voice
            voice = is_voice_message(payload, body)
            if voice:
                url, mime = voice
                try:
                    from ai import transcribe_voice
                    audio = await download_file(url)
                    if audio:
                        text = await transcribe_voice(audio, mime)
                        if text:
                            logger.info(f"[{chat_id}] Voice: {text[:80]}")
                            await handle_message(chat_id, sender_name, text)
                except Exception as e:
                    logger.error(f"[{chat_id}] Voice error: {e}", exc_info=True)
                if receipt_id is not None:
                    await delete_notification(receipt_id)
                continue

            # Text
            text = extract_text(payload, body)
            if text:
                logger.info(f"[{chat_id}] {sender_name}: {text[:80]}")
                asyncio.create_task(handle_message(chat_id, sender_name, text))

            if receipt_id is not None:
                await delete_notification(receipt_id)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Poll error: {e}", exc_info=True)
            await asyncio.sleep(interval)


# ── App ──────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Sales Ottenok v2...")
    init_db()
    await load_photo_index()

    tasks = []
    if GREEN_API_POLLING:
        tasks.append(asyncio.create_task(poll_loop(GREEN_API_POLL_INTERVAL)))
    tasks.append(asyncio.create_task(nudge_loop()))

    logger.info("Bot ready!")
    yield

    for t in tasks:
        t.cancel()
    logger.info("Bot stopped.")


app = FastAPI(title="Sales Ottenok v2", lifespan=lifespan)
_start = _time.time()


@app.get("/health")
async def health():
    return {"status": "ok", "uptime": int(_time.time() - _start)}


@app.post("/webhook")
async def webhook(request: Request):
    """Webhook endpoint (используется если polling выключен)."""
    if GREEN_API_POLLING:
        return Response(status_code=200)

    try:
        body = await request.json()
    except Exception:
        return Response(status_code=400)

    type_wh = body.get("typeWebhook", "")

    if type_wh == "outgoingMessageReceived":
        asyncio.create_task(handle_outgoing_message(body))
        return Response(status_code=200)

    if type_wh != "incomingMessageReceived":
        return Response(status_code=200)

    try:
        payload = WebhookPayload(**body)
    except Exception:
        return Response(status_code=200)

    if not payload.senderData or not payload.messageData:
        return Response(status_code=200)

    chat_id = payload.senderData.chatId
    sender_name = payload.senderData.senderName or ""

    # Voice
    voice = is_voice_message(payload, body)
    if voice:
        url, mime = voice
        async def _process_voice():
            try:
                from ai import transcribe_voice
                from greenapi_client import download_file
                audio = await download_file(url)
                if audio:
                    text = await transcribe_voice(audio, mime)
                    if text:
                        await handle_message(chat_id, sender_name, text)
            except Exception as e:
                logger.error(f"[{chat_id}] Voice error: {e}", exc_info=True)
        asyncio.create_task(_process_voice())
        return Response(status_code=200)

    text = extract_text(payload, body)
    if text:
        logger.info(f"[{chat_id}] {sender_name}: {text[:80]}")
        asyncio.create_task(handle_message(chat_id, sender_name, text))

    return Response(status_code=200)


if __name__ == "__main__":
    uvicorn.run("main:app", host=WEBHOOK_HOST, port=WEBHOOK_PORT, reload=False)
