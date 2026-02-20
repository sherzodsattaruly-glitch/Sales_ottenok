"""Внешние сервисы: Google Sheets (каталог), Google Drive (фото), Telegram, N8N."""

import asyncio
import logging
import re
import time
import json
from functools import lru_cache

import httpx

from config import (
    GOOGLE_CREDENTIALS_FILE,
    GOOGLE_DRIVE_PHOTOS_FOLDER_ID,
    CATALOG_SHEETS_ID,
    TELEGRAM_ALERT_BOT_TOKEN,
    TELEGRAM_ALERT_CHAT_ID,
    N8N_ORDER_WEBHOOK_URL,
)

logger = logging.getLogger(__name__)

# ── Google Auth ──────────────────────────────────────────────

_google_service_sheets = None
_google_service_drive = None


def _get_google_creds():
    from google.oauth2 import service_account
    return service_account.Credentials.from_service_account_file(
        GOOGLE_CREDENTIALS_FILE,
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets.readonly",
            "https://www.googleapis.com/auth/drive.readonly",
        ],
    )


def _sheets_service():
    global _google_service_sheets
    if _google_service_sheets is None:
        from googleapiclient.discovery import build
        _google_service_sheets = build("sheets", "v4", credentials=_get_google_creds())
    return _google_service_sheets


def _drive_service():
    global _google_service_drive
    if _google_service_drive is None:
        from googleapiclient.discovery import build
        _google_service_drive = build("drive", "v3", credentials=_get_google_creds())
    return _google_service_drive


# ── Каталог (Google Sheets) ──────────────────────────────────

_catalog_cache: list[dict] = []
_catalog_cache_ts: float = 0
CATALOG_TTL = 300  # 5 мин


async def get_catalog() -> list[dict]:
    """Загрузить каталог товаров из Google Sheets. Кэш 5 мин."""
    global _catalog_cache, _catalog_cache_ts
    if _catalog_cache and (time.time() - _catalog_cache_ts) < CATALOG_TTL:
        return _catalog_cache

    if not CATALOG_SHEETS_ID:
        logger.warning("CATALOG_SHEETS_ID not set")
        return _catalog_cache

    try:
        def _load():
            svc = _sheets_service()
            result = svc.spreadsheets().values().get(
                spreadsheetId=CATALOG_SHEETS_ID,
                range="A:Z",  # все колонки
            ).execute()
            rows = result.get("values", [])
            if len(rows) < 2:
                return []
            headers = [h.strip().lower() for h in rows[0]]
            items = []
            for row in rows[1:]:
                item = {}
                for i, h in enumerate(headers):
                    item[h] = row[i].strip() if i < len(row) else ""
                if item.get("product_name") or item.get("название"):
                    items.append(item)
            return items

        _catalog_cache = await asyncio.get_event_loop().run_in_executor(None, _load)
        _catalog_cache_ts = time.time()
        logger.info(f"Catalog loaded: {len(_catalog_cache)} items")
    except Exception as e:
        logger.error(f"Failed to load catalog: {e}")

    return _catalog_cache


def format_catalog_for_prompt(catalog: list[dict]) -> str:
    """Формат каталога для system prompt."""
    if not catalog:
        return "Каталог пуст — скажи клиенту, что сейчас уточнишь наличие."
    lines = []
    for item in catalog:
        name = item.get("product_name") or item.get("название", "?")
        price = item.get("price") or item.get("цена", "")
        sizes = item.get("sizes") or item.get("размеры", "")
        colors = item.get("colors") or item.get("цвета", "")
        qty = item.get("quantity") or item.get("количество", "")
        line = f"- {name}"
        if price:
            line += f" | {price}"
        if sizes:
            line += f" | размеры: {sizes}"
        if colors:
            line += f" | цвета: {colors}"
        if qty:
            line += f" | наличие: {qty}"
        lines.append(line)
    return "\n".join(lines)


# ── Проверка наличия ─────────────────────────────────────────

async def check_stock(product: str, size: str = "", color: str = "") -> dict:
    """Проверить наличие товара в каталоге."""
    catalog = await get_catalog()
    product_lower = product.lower()
    matches = []
    for item in catalog:
        name = (item.get("product_name") or item.get("название", "")).lower()
        if product_lower in name or name in product_lower:
            matches.append(item)

    if not matches:
        return {"available": False, "message": f"Товар '{product}' не найден в каталоге"}

    # Фильтруем по размеру/цвету если указаны
    for item in matches:
        item_sizes = (item.get("sizes") or item.get("размеры", "")).lower()
        item_colors = (item.get("colors") or item.get("цвета", "")).lower()
        item_qty = item.get("quantity") or item.get("количество", "0")

        size_ok = not size or size.lower() in item_sizes
        color_ok = not color or color.lower() in item_colors
        in_stock = str(item_qty).strip() not in ("0", "")

        if size_ok and color_ok and in_stock:
            return {
                "available": True,
                "product": item.get("product_name") or item.get("название"),
                "price": item.get("price") or item.get("цена", ""),
                "sizes": item.get("sizes") or item.get("размеры", ""),
                "colors": item.get("colors") or item.get("цвета", ""),
            }

    return {"available": False, "message": f"Товар '{product}' в указанной комплектации отсутствует"}


# ── Фото (Google Drive) ─────────────────────────────────────

_photo_index: dict[str, list[dict]] = {}  # product_key -> [{file_id, name}]
_photo_index_ts: float = 0
PHOTO_INDEX_TTL = 1800  # 30 мин


async def load_photo_index():
    """Загрузить индекс фото из Google Drive."""
    global _photo_index, _photo_index_ts
    if _photo_index and (time.time() - _photo_index_ts) < PHOTO_INDEX_TTL:
        return _photo_index

    if not GOOGLE_DRIVE_PHOTOS_FOLDER_ID:
        return _photo_index

    try:
        def _load():
            svc = _drive_service()
            # Сначала проверяем подпапки (продукт = папка)
            folders = svc.files().list(
                q=f"'{GOOGLE_DRIVE_PHOTOS_FOLDER_ID}' in parents and mimeType='application/vnd.google-apps.folder'",
                fields="files(id, name)",
                pageSize=200,
            ).execute().get("files", [])

            index = {}

            if folders:
                # Структура: корневая/продукт_папка/фото.jpg
                for folder in folders:
                    photos = svc.files().list(
                        q=f"'{folder['id']}' in parents and mimeType contains 'image/'",
                        fields="files(id, name)",
                        pageSize=50,
                    ).execute().get("files", [])
                    if photos:
                        key = folder["name"].lower().strip()
                        index[key] = [{"file_id": p["id"], "name": p["name"]} for p in photos]
            else:
                # Плоская структура: фото лежат прямо в корневой папке
                # Группируем по имени продукта (убираем номер фото в конце)
                # "Chanel балетки бежевые 1.jpg" → "chanel балетки бежевые"
                images = svc.files().list(
                    q=f"'{GOOGLE_DRIVE_PHOTOS_FOLDER_ID}' in parents and mimeType contains 'image/'",
                    fields="files(id, name)",
                    pageSize=500,
                ).execute().get("files", [])
                for img in images:
                    name = img["name"]
                    # Убираем расширение и номер фото: "Product Name 1.jpg" → "product name"
                    base = re.sub(r'\.\w+$', '', name)           # убрать .jpg/.png
                    key = re.sub(r'\s+\d+$', '', base).lower().strip()  # убрать " 1"
                    if key not in index:
                        index[key] = []
                    index[key].append({"file_id": img["id"], "name": name})
            return index

        _photo_index = await asyncio.get_event_loop().run_in_executor(None, _load)
        _photo_index_ts = time.time()
        logger.info(f"Photo index loaded: {len(_photo_index)} products")
    except Exception as e:
        logger.error(f"Failed to load photo index: {e}")

    return _photo_index


async def find_photos(product: str, color: str = "", max_photos: int = 6) -> list[dict]:
    """Найти фото товара. Возвращает [{file_id, name, caption}]."""
    index = await load_photo_index()
    if not index:
        return []

    product_lower = product.lower().strip()
    color_lower = color.lower().strip() if color else ""

    # Ищем папку по совпадению
    best_key = None
    best_score = 0
    for key in index:
        # Точное совпадение
        if product_lower == key:
            best_key = key
            break
        # Частичное
        if product_lower in key or key in product_lower:
            score = len(set(product_lower.split()) & set(key.split()))
            if score > best_score:
                best_score = score
                best_key = key

    if not best_key:
        return []

    photos = index[best_key]

    # Фильтруем по цвету если указан
    if color_lower:
        color_filtered = [p for p in photos if color_lower in p["name"].lower()]
        if color_filtered:
            photos = color_filtered

    # Ограничиваем
    photos = photos[:max_photos]
    return [{"file_id": p["file_id"], "filename": p["name"], "caption": ""} for p in photos]


async def download_drive_file(file_id: str) -> bytes:
    """Скачать файл из Google Drive."""
    def _download():
        svc = _drive_service()
        from io import BytesIO
        from googleapiclient.http import MediaIoBaseDownload
        request = svc.files().get_media(fileId=file_id)
        buf = BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buf.getvalue()
    return await asyncio.get_event_loop().run_in_executor(None, _download)


# ── Telegram алерты ──────────────────────────────────────────

_last_alert: dict[str, float] = {}
ALERT_THROTTLE = 600


async def notify_error(error_type: str, message: str):
    if not TELEGRAM_ALERT_BOT_TOKEN or not TELEGRAM_ALERT_CHAT_ID:
        return
    now = time.time()
    if now - _last_alert.get(error_type, 0) < ALERT_THROTTLE:
        return
    _last_alert[error_type] = now
    text = f"⚠️ Sales Ottenok\n\nType: {error_type}\n{message[:1000]}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_ALERT_BOT_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_ALERT_CHAT_ID, "text": text},
            )
    except Exception as e:
        logger.warning(f"Telegram alert failed: {e}")


async def notify_order(order: dict):
    """Уведомить менеджера о новом заказе через Telegram."""
    if TELEGRAM_ALERT_BOT_TOKEN and TELEGRAM_ALERT_CHAT_ID:
        text = f"🛍 Новый заказ!\n\n"
        text += f"Товар: {order.get('product', '?')}\n"
        text += f"Размер: {order.get('size', '-')}\n"
        text += f"Цвет: {order.get('color', '-')}\n"
        text += f"Город: {order.get('city', '?')}\n"
        text += f"Адрес: {order.get('address', '?')}\n"
        text += f"Клиент: {order.get('client_phone', '?')}"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"https://api.telegram.org/bot{TELEGRAM_ALERT_BOT_TOKEN}/sendMessage",
                    json={"chat_id": TELEGRAM_ALERT_CHAT_ID, "text": text},
                )
        except Exception as e:
            logger.warning(f"Order notification failed: {e}")


# ── N8N webhook ──────────────────────────────────────────────

async def send_order_to_n8n(order: dict):
    if not N8N_ORDER_WEBHOOK_URL:
        return
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            await client.post(N8N_ORDER_WEBHOOK_URL, json=order)
        logger.info(f"Order sent to N8N: {order.get('product')}")
    except Exception as e:
        logger.error(f"N8N webhook failed: {e}")
