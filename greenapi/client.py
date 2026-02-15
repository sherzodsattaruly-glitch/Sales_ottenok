"""
Green API клиент для отправки сообщений и медиа в WhatsApp.
"""

import asyncio
import logging
from functools import wraps

import httpx

from config import GREEN_API_INSTANCE_ID, GREEN_API_TOKEN

logger = logging.getLogger(__name__)

BASE_URL = f"https://api.green-api.com/waInstance{GREEN_API_INSTANCE_ID}"


def retry_async(max_retries: int = 3, delay: float = 1.0, backoff: float = 2.0):
    """Декоратор для повторных попыток с экспоненциальной задержкой."""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            current_delay = delay
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    logger.warning(
                        f"{func.__name__} попытка {attempt + 1}/{max_retries} failed: {e}"
                    )
                    if attempt < max_retries - 1:
                        await asyncio.sleep(current_delay)
                        current_delay *= backoff
            logger.error(f"{func.__name__} failed after {max_retries} attempts")
            raise last_exception
        return wrapper
    return decorator


@retry_async(max_retries=3, delay=1.0)
async def send_text(chat_id: str, text: str) -> dict:
    """Отправить текстовое сообщение."""
    url = f"{BASE_URL}/sendMessage/{GREEN_API_TOKEN}"
    payload = {
        "chatId": chat_id,
        "message": text
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        result = response.json()
        logger.info(f"Sent text to {chat_id}: {text[:50]}...")
        return result


@retry_async(max_retries=3, delay=1.0)
async def send_contact(chat_id: str, phone_contact: int, first_name: str, last_name: str = "") -> dict:
    """Отправить контактную карточку."""
    url = f"{BASE_URL}/sendContact/{GREEN_API_TOKEN}"
    payload = {
        "chatId": chat_id,
        "contact": {
            "phoneContact": phone_contact,
            "firstName": first_name,
            "lastName": last_name
        }
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        result = response.json()
        logger.info(f"Sent contact to {chat_id}: {first_name} {last_name} ({phone_contact})")
        return result


@retry_async(max_retries=3, delay=1.0)
async def receive_notification() -> dict | None:
    """Получить одно уведомление (polling)."""
    url = f"{BASE_URL}/receiveNotification/{GREEN_API_TOKEN}"
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url)
        # Некоторые аккаунты возвращают 400, когда уведомлений нет
        if response.status_code == 400:
            return None
        response.raise_for_status()
        # API может вернуть null
        if not response.content or response.content.strip() in (b"null", b""):
            return None
        return response.json()


@retry_async(max_retries=3, delay=1.0)
async def delete_notification(receipt_id: int) -> dict:
    """Удалить уведомление после обработки."""
    url = f"{BASE_URL}/deleteNotification/{GREEN_API_TOKEN}/{receipt_id}"
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.delete(url)
        response.raise_for_status()
        return response.json()


@retry_async(max_retries=3, delay=2.0)
async def send_image_by_url(chat_id: str, image_url: str, caption: str = "", filename: str = "photo.jpg") -> dict:
    """Отправить изображение по URL."""
    url = f"{BASE_URL}/sendFileByUrl/{GREEN_API_TOKEN}"
    payload = {
        "chatId": chat_id,
        "urlFile": image_url,
        "fileName": filename,
        "caption": caption
    }
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        result = response.json()
        logger.info(f"Sent image to {chat_id}: {filename}")
        return result


@retry_async(max_retries=3, delay=2.0)
async def send_image_by_upload(chat_id: str, file_bytes: bytes, caption: str = "", filename: str = "photo.jpg") -> dict:
    """Отправить изображение загрузкой бинарных данных."""
    url = f"{BASE_URL}/sendFileByUpload/{GREEN_API_TOKEN}"
    async with httpx.AsyncClient(timeout=60) as client:
        files = {"file": (filename, file_bytes, "image/jpeg")}
        data = {"chatId": chat_id, "caption": caption}
        response = await client.post(url, data=data, files=files)
        response.raise_for_status()
        result = response.json()
        logger.info(f"Sent image (upload) to {chat_id}: {filename}")
        return result


async def download_voice_message(download_url: str) -> bytes:
    """Скачать голосовое сообщение по downloadUrl из Green API."""
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(download_url)
        response.raise_for_status()
        return response.content


async def send_multiple_images(chat_id: str, images: list[dict]) -> None:
    """
    Отправить несколько изображений последовательно.
    Каждый dict: {'file_id': str, 'caption': str, 'filename': str}
    Скачивает из Google Drive через API и загружает в Green API.
    """
    from gdrive.client import download_file_bytes

    for img in images:
        try:
            file_id = img.get("file_id")
            if not file_id:
                logger.warning(f"No file_id for image {img.get('filename')}, skipping")
                continue

            # Скачиваем из Google Drive через сервисный аккаунт
            file_bytes = await asyncio.get_event_loop().run_in_executor(
                None, download_file_bytes, file_id
            )

            # Загружаем в Green API
            await send_image_by_upload(
                chat_id,
                file_bytes,
                img.get("caption", ""),
                img.get("filename", "photo.jpg")
            )
            await asyncio.sleep(0.5)  # пауза между отправками
        except Exception as e:
            logger.error(f"Failed to send image {img.get('filename')}: {e}")
