"""Green API клиент — отправка сообщений и медиа в WhatsApp."""

from __future__ import annotations

import asyncio
import logging
from functools import wraps

import httpx

from config import GREEN_API_INSTANCE_ID, GREEN_API_TOKEN

logger = logging.getLogger(__name__)

BASE_URL = f"https://api.green-api.com/waInstance{GREEN_API_INSTANCE_ID}"

# Singleton httpx client — избегаем создание нового соединения на каждый запрос
_http_client: httpx.AsyncClient | None = None


def _get_client(timeout: float = 30) -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=timeout)
    return _http_client


def _retry(max_retries=3, delay=1.0, backoff=2.0):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exc = None
            d = delay
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_exc = e
                    logger.warning(f"{func.__name__} attempt {attempt+1}/{max_retries}: {e}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(d)
                        d *= backoff
            raise last_exc
        return wrapper
    return decorator


@_retry()
async def send_text(chat_id: str, text: str) -> dict:
    url = f"{BASE_URL}/sendMessage/{GREEN_API_TOKEN}"
    client = _get_client()
    r = await client.post(url, json={"chatId": chat_id, "message": text})
    r.raise_for_status()
    logger.info(f"[{chat_id}] Sent: {text[:80]}")
    return r.json()


@_retry(delay=2.0)
async def send_image_by_upload(chat_id: str, file_bytes: bytes, caption: str = "", filename: str = "photo.jpg") -> dict:
    url = f"{BASE_URL}/sendFileByUpload/{GREEN_API_TOKEN}"
    client = _get_client(timeout=60)
    r = await client.post(url, data={"chatId": chat_id, "caption": caption}, files={"file": (filename, file_bytes, "image/jpeg")})
    r.raise_for_status()
    logger.info(f"[{chat_id}] Sent image: {filename}")
    return r.json()


@_retry()
async def receive_notification() -> dict | None:
    url = f"{BASE_URL}/receiveNotification/{GREEN_API_TOKEN}"
    client = _get_client()
    r = await client.get(url)
    if r.status_code == 400:
        return None
    r.raise_for_status()
    if not r.content or r.content.strip() in (b"null", b""):
        return None
    return r.json()


@_retry()
async def delete_notification(receipt_id: int) -> dict:
    url = f"{BASE_URL}/deleteNotification/{GREEN_API_TOKEN}/{receipt_id}"
    client = _get_client()
    r = await client.delete(url)
    r.raise_for_status()
    return r.json()


@_retry(delay=2.0)
async def download_file(url: str) -> bytes:
    client = _get_client(timeout=30)
    r = await client.get(url)
    r.raise_for_status()
    return r.content


async def send_photos(chat_id: str, photos: list[dict]):
    """Send multiple photos. Each dict: {file_id, caption?, filename?}."""
    from services import get_photo_bytes
    for photo in photos:
        try:
            file_bytes = get_photo_bytes(photo["file_id"])
            if not file_bytes:
                logger.warning(f"[{chat_id}] Photo not in cache: {photo.get('filename', photo['file_id'])}")
                continue
            await send_image_by_upload(chat_id, file_bytes, photo.get("caption", ""), photo.get("filename", "photo.jpg"))
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"[{chat_id}] Failed to send photo: {e}")
