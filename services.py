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
    ORDER_GROUP_CHAT_ID,
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
    """Загрузить каталог товаров из Google Sheets. Кэш 5 мин.

    Нормализует колонки Sheet → единый формат:
      product_name, category, price, colors, description, sizes, quantity
    Размеры агрегируются из отдельных колонок (35-42) с кол-вом > 0.
    """
    global _catalog_cache, _catalog_cache_ts
    if _catalog_cache and (time.time() - _catalog_cache_ts) < CATALOG_TTL:
        return _catalog_cache

    if not CATALOG_SHEETS_ID:
        logger.warning("CATALOG_SHEETS_ID not set")
        return _catalog_cache

    # Маппинг заголовков Sheet → нормализованные имена
    _HEADER_MAP = {
        "name": "product_name",
        "название": "product_name",
        "product_name": "product_name",
        "category": "category",
        "категория": "category",
        "price": "price",
        "цена": "price",
        "colors": "colors",
        "цвета": "colors",
        "color": "colors",
        "цвет": "colors",
        "descriptions": "description",
        "description": "description",
        "описание": "description",
        "sizes": "sizes",
        "размеры": "sizes",
        "quantity": "quantity",
        "количество": "quantity",
        "кол-во": "quantity",
        "кол-во сумки": "bag_quantity",
        "спец цена": "special_price",
        "спец. цена": "special_price",
        "special_price": "special_price",
    }
    # Колонки-размеры (числа = номера размеров обуви)
    _SIZE_COLUMNS = {"35", "36", "37", "38", "39", "40", "41", "42", "43", "44"}

    try:
        def _load():
            svc = _sheets_service()
            result = svc.spreadsheets().values().get(
                spreadsheetId=CATALOG_SHEETS_ID,
                range="A:Z",
            ).execute()
            rows = result.get("values", [])
            if len(rows) < 2:
                return []
            raw_headers = [h.strip() for h in rows[0]]
            items = []
            for row in rows[1:]:
                raw = {}
                for i, h in enumerate(raw_headers):
                    raw[h] = row[i].strip() if i < len(row) else ""

                # Нормализуем поля
                item = {}
                size_avail = {}  # размер → кол-во
                for h, val in raw.items():
                    h_lower = h.lower()
                    if h_lower in _SIZE_COLUMNS:
                        try:
                            qty = int(val) if val else 0
                        except ValueError:
                            qty = 0
                        if qty > 0:
                            size_avail[h_lower] = qty
                    elif h_lower in _HEADER_MAP:
                        item[_HEADER_MAP[h_lower]] = val

                # Агрегируем размеры
                if size_avail:
                    item["sizes"] = ", ".join(sorted(size_avail.keys(), key=int))
                    item["quantity"] = str(sum(size_avail.values()))
                elif not item.get("sizes"):
                    # Для сумок/аксессуаров — берём bag_quantity
                    bag_qty = item.pop("bag_quantity", "0")
                    item["sizes"] = ""
                    if not item.get("quantity"):
                        item["quantity"] = bag_qty
                item.pop("bag_quantity", None)

                if item.get("product_name"):
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
        name = item.get("product_name", "?")
        category = item.get("category", "")
        price = item.get("price", "")
        sizes = item.get("sizes", "")
        colors = item.get("colors", "")
        qty = item.get("quantity", "")
        line = f"- {name}"
        if category:
            line += f" ({category})"
        if price:
            line += f" | {price}"
        if sizes:
            line += f" | размеры: {sizes}"
        if colors:
            line += f" | цвета: {colors}"
        if qty:
            line += f" | наличие: {qty}"
        special_price = item.get("special_price", "")
        if special_price:
            line += f" | спеццена: {special_price} (до 1 марта)"
        lines.append(line)
    return "\n".join(lines)


# ── Проверка наличия ─────────────────────────────────────────

async def check_stock(product: str, size: str = "", color: str = "") -> dict:
    """Проверить наличие товара в каталоге.

    Ищет по имени товара и категории (туфли, сумка, кроссовки и т.д.).
    """
    catalog = await get_catalog()
    product_lower = product.lower()
    matches = []
    for item in catalog:
        name = item.get("product_name", "").lower()
        category = item.get("category", "").lower()
        # Поиск по имени или категории
        if (product_lower in name or name in product_lower
                or product_lower in category or category in product_lower):
            matches.append(item)

    if not matches:
        return {"available": False, "message": f"Товар '{product}' не найден в каталоге"}

    # Собираем все подходящие варианты
    available_items = []
    for item in matches:
        item_sizes = item.get("sizes", "").lower()
        item_colors = item.get("colors", "").lower().strip()
        item_qty = item.get("quantity", "0")

        size_ok = not size or size.lower() in item_sizes
        color_ok = not color or color.lower() in item_colors
        in_stock = str(item_qty).strip() not in ("0", "")

        if size_ok and color_ok and in_stock:
            available_items.append({
                "product": item.get("product_name"),
                "price": item.get("price", ""),
                "special_price": item.get("special_price", ""),
                "sizes": item.get("sizes", ""),
                "colors": item.get("colors", ""),
            })

    if available_items:
        return {"available": True, "items": available_items}

    return {"available": False, "message": f"Товар '{product}' в указанной комплектации отсутствует"}


# ── Фото (Google Drive) ─────────────────────────────────────

_photo_index: dict[str, list[dict]] = {}  # product_key -> [{file_id, name}]
_photo_bytes: dict[str, bytes] = {}  # file_id -> image bytes
_photo_index_ts: float = 0
PHOTO_INDEX_TTL = 1800  # 30 мин


def get_photo_bytes(file_id: str) -> bytes | None:
    """Получить байты фото из in-memory кэша."""
    return _photo_bytes.get(file_id)


async def load_photo_index():
    """Загрузить индекс + байты всех фото из Google Drive в память. Retry до 3 раз."""
    global _photo_index, _photo_bytes, _photo_index_ts
    if _photo_index and (time.time() - _photo_index_ts) < PHOTO_INDEX_TTL:
        return _photo_index

    if not GOOGLE_DRIVE_PHOTOS_FOLDER_ID:
        return _photo_index

    def _load():
        from io import BytesIO
        from googleapiclient.http import MediaIoBaseDownload

        svc = _drive_service()

        # 1. Собираем индекс (метаданные)
        folders = svc.files().list(
            q=f"'{GOOGLE_DRIVE_PHOTOS_FOLDER_ID}' in parents and mimeType='application/vnd.google-apps.folder'",
            fields="files(id, name)",
            pageSize=200,
        ).execute().get("files", [])

        index = {}
        all_file_ids = []

        if folders:
            for folder in folders:
                photos = svc.files().list(
                    q=f"'{folder['id']}' in parents and mimeType contains 'image/'",
                    fields="files(id, name)",
                    pageSize=50,
                ).execute().get("files", [])
                if photos:
                    key = folder["name"].lower().strip()
                    index[key] = [{"file_id": p["id"], "name": p["name"]} for p in photos]
                    all_file_ids.extend(p["id"] for p in photos)
        else:
            images = svc.files().list(
                q=f"'{GOOGLE_DRIVE_PHOTOS_FOLDER_ID}' in parents and mimeType contains 'image/'",
                fields="files(id, name)",
                pageSize=500,
            ).execute().get("files", [])
            for img in images:
                name = img["name"]
                base = re.sub(r'\.\w+$', '', name)
                key = re.sub(r'\s+\d+$', '', base).lower().strip()
                if key not in index:
                    index[key] = []
                index[key].append({"file_id": img["id"], "name": name})
                all_file_ids.append(img["id"])

        # 2. Скачиваем все фото в память (последовательно, один Drive service)
        bytes_cache = {}
        for fid in all_file_ids:
            try:
                request = svc.files().get_media(fileId=fid)
                buf = BytesIO()
                downloader = MediaIoBaseDownload(buf, request)
                done = False
                while not done:
                    _, done = downloader.next_chunk()
                bytes_cache[fid] = buf.getvalue()
            except Exception as e:
                logger.warning(f"Failed to download photo {fid}: {e}")

        return index, bytes_cache

    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            index, bytes_cache = await asyncio.get_event_loop().run_in_executor(None, _load)
            if index:
                _photo_index = index
                _photo_bytes = bytes_cache
                _photo_index_ts = time.time()
                total_mb = sum(len(b) for b in bytes_cache.values()) / 1024 / 1024
                logger.info(f"Photo cache loaded: {len(index)} products, {len(bytes_cache)} files, {total_mb:.1f} MB")
                return _photo_index
            else:
                logger.warning(f"Photo index empty (attempt {attempt}/{max_attempts})")
                if attempt < max_attempts:
                    await asyncio.sleep(2 * attempt)
        except Exception as e:
            logger.error(f"Failed to load photo index (attempt {attempt}/{max_attempts}): {e}")
            if attempt < max_attempts:
                await asyncio.sleep(2 * attempt)

    logger.error("Photo index: all attempts failed, returning cached or empty")
    return _photo_index


_RUSSIAN_COLORS = {
    "черная", "черные", "чёрная", "чёрные", "черный",
    "белая", "белые", "белый",
    "бежевая", "бежевые", "бежевый",
    "розовая", "розовые", "розовый",
    "красная", "красные", "красный",
    "синяя", "синие", "синий",
    "голубая", "голубые", "голубой",
    "серая", "серые", "серый",
    "зеленая", "зеленые",
    "коричневая", "коричневые",
    "бордовая", "бордовые",
    "серебряная", "серебряные", "серебристая", "серебристые",
    "золотая", "золотые", "золотистая", "золотистые",
    "молочная", "молочные",
    "пудровая", "пудровые",
    "нюдовая", "нюдовые",
}


def _extract_color_from_filename(filename: str) -> str:
    """Извлечь русское слово цвета из имени файла фото."""
    base = re.sub(r'\.\w+$', '', filename)
    for word in base.lower().split():
        if word in _RUSSIAN_COLORS:
            return word
    return ""


def _make_photo_caption(product_key: str, catalog: list[dict], color: str = "") -> str:
    """Сформировать подпись к фото: название товара + цвет."""
    display_name = product_key.title()

    # Поиск названия в каталоге по пересечению токенов
    key_tokens = set(product_key.lower().split())
    best_overlap = 0
    for item in catalog:
        name = item.get("product_name", "").lower()
        name_tokens = set(name.split())
        overlap = len(key_tokens & name_tokens)
        if overlap > best_overlap:
            best_overlap = overlap
            display_name = item.get("product_name", display_name)

    if color:
        return f"{display_name}, {color}"
    return display_name


async def find_photos(product: str, color: str = "", max_photos: int = 6) -> list[dict]:
    """Найти фото товара. Возвращает [{file_id, filename, caption}].

    Если запрос общий (например "туфли"), возвращает по 1 фото от каждого
    подходящего товара. Если конкретный ("Chanel slingbacks"), возвращает
    все фото этого товара.
    """
    index = await load_photo_index()
    if not index:
        return []

    product_lower = product.lower().strip()
    color_lower = color.lower().strip() if color else ""
    query_tokens = set(product_lower.split())

    # Собираем все подходящие ключи с score
    matched: list[tuple[str, int]] = []
    for key in index:
        if product_lower == key:
            matched.append((key, 100))  # точное совпадение
            continue
        # Слово-в-слово overlap
        key_tokens = set(key.split())
        overlap = len(query_tokens & key_tokens)
        if overlap > 0:
            matched.append((key, overlap))
        elif product_lower in key or key in product_lower:
            matched.append((key, 1))

    if not matched:
        return []

    # Сортируем по score desc
    matched.sort(key=lambda x: x[1], reverse=True)

    # Отсекаем слабые совпадения (score 1) когда есть сильные (score >= 2)
    best_score = matched[0][1]
    if best_score >= 2:
        matched = [(k, s) for k, s in matched if s >= 2]

    # Загружаем каталог для подписей (цена)
    catalog = await get_catalog()

    # Собираем фото
    result = []
    # Конкретный товар: 1 матч, точный матч, или один явный лидер по score
    is_specific = (
        len(matched) == 1
        or matched[0][1] == 100
        or (len(matched) >= 2 and matched[0][1] > matched[1][1])
    )
    if is_specific:
        # Конкретный товар — все его фото с цветом из имени файла
        key = matched[0][0]
        photos = index[key]
        if color_lower:
            filtered = [p for p in photos if color_lower in p["name"].lower()]
            if filtered:
                photos = filtered
        for p in photos[:max_photos]:
            photo_color = _extract_color_from_filename(p["name"])
            caption = _make_photo_caption(key, catalog, photo_color)
            result.append({"file_id": p["file_id"], "filename": p["name"], "caption": caption})
    else:
        # Общий запрос (туфли, сумки) — по 1 фото от каждого товара с цветом
        for key, _ in matched:
            photos = index[key]
            if color_lower:
                filtered = [p for p in photos if color_lower in p["name"].lower()]
                if filtered:
                    photos = filtered
            if photos:
                photo_color = _extract_color_from_filename(photos[0]["name"])
                caption = _make_photo_caption(key, catalog, photo_color)
                result.append({"file_id": photos[0]["file_id"], "filename": photos[0]["name"], "caption": caption})
            if len(result) >= max_photos:
                break

    return result




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


async def notify_order_whatsapp(order: dict):
    """Отправить сводку заказа в WhatsApp-группу менеджеров."""
    if not ORDER_GROUP_CHAT_ID:
        return
    from greenapi_client import send_text
    text = (
        f"🛍 Новый заказ!\n\n"
        f"Товар: {order.get('product', '?')}\n"
        f"Размер: {order.get('size', '-')}\n"
        f"Цвет: {order.get('color', '-')}\n"
        f"Город: {order.get('city', '?')}\n"
        f"Адрес: {order.get('address', '?')}\n"
        f"Телефон клиента: {order.get('client_phone', '?')}"
    )
    try:
        await send_text(ORDER_GROUP_CHAT_ID, text)
        logger.info(f"Order sent to WhatsApp group: {order.get('product')}")
    except Exception as e:
        logger.error(f"WhatsApp group notification failed: {e}")


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
