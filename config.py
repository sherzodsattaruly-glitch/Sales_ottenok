"""
Конфигурация бота Оттенок.
Все секреты загружаются из .env файла.
"""

import os
import re
from pathlib import Path
from dotenv import load_dotenv

# Загружаем .env из корня проекта
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# Green API
GREEN_API_INSTANCE_ID = os.getenv("GREEN_API_INSTANCE_ID", "")
GREEN_API_TOKEN = os.getenv("GREEN_API_TOKEN", "")

# OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
OPENAI_EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")

# Google Drive
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials/google_credentials.json")
GOOGLE_DRIVE_PHOTOS_FOLDER_ID = os.getenv("GOOGLE_DRIVE_PHOTOS_FOLDER_ID", "")

# Paths
CHROMA_DB_PATH = os.getenv("CHROMA_DB_PATH", "data/chroma_db")
SQLITE_DB_PATH = os.getenv("SQLITE_DB_PATH", "data/ottenok.db")
KNOWLEDGE_BASE_PATH = os.getenv("KNOWLEDGE_BASE_PATH", "data/knowledge_base")

# Server
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "0.0.0.0")
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8080"))

# Bot behavior
MAX_CONVERSATION_HISTORY = int(os.getenv("MAX_CONVERSATION_HISTORY", "20"))
MAX_RAG_RESULTS = int(os.getenv("MAX_RAG_RESULTS", "5"))
MAX_PHOTOS_PER_MESSAGE = int(os.getenv("MAX_PHOTOS_PER_MESSAGE", "3"))
# При показе товара с разными цветами: макс. фото всего и макс. одного цвета (2 розовых, 2 черных, 2 бежевых = 6)
MAX_PHOTOS_PRODUCT_SHOWCASE = int(os.getenv("MAX_PHOTOS_PRODUCT_SHOWCASE", "6"))
MAX_PHOTOS_PER_COLOR = int(os.getenv("MAX_PHOTOS_PER_COLOR", "2"))

# Manager handoff
def _normalize_chat_id(value: str) -> str:
    v = (value or "").strip()
    if not v:
        return ""
    if "@c.us" in v:
        return v
    digits = re.sub(r"\D", "", v)
    if not digits:
        return ""
    return f"{digits}@c.us"


_manager_raw = os.getenv("MANAGER_NUMBERS", "")
MANAGER_CHAT_IDS = {cid for cid in (_normalize_chat_id(x) for x in _manager_raw.split(",")) if cid}

# Green API polling (fallback when webhooks are not delivered)
GREEN_API_POLLING = os.getenv("GREEN_API_POLLING", "1").lower() in ("1", "true", "yes")
GREEN_API_POLL_INTERVAL = float(os.getenv("GREEN_API_POLL_INTERVAL", "2.0"))

# Inventory (Excel)
INVENTORY_EXCEL_PATH = os.getenv("INVENTORY_EXCEL_PATH", "data/inventory.xlsx")
INVENTORY_CACHE_TTL = int(os.getenv("INVENTORY_CACHE_TTL", "300"))  # 5 минут

# Catalog (Google Sheets)
CATALOG_SHEETS_ID = os.getenv("CATALOG_SHEETS_ID", "")
CATALOG_CACHE_TTL = int(os.getenv("CATALOG_CACHE_TTL", "300"))  # 5 минут

# Telegram alerts
TELEGRAM_ALERT_BOT_TOKEN = os.getenv("TELEGRAM_ALERT_BOT_TOKEN", "")
TELEGRAM_ALERT_CHAT_ID = os.getenv("TELEGRAM_ALERT_CHAT_ID", "")

# Nudge Scheduler
NUDGE_ENABLED = os.getenv("NUDGE_ENABLED", "1").lower() in ("1", "true", "yes")
NUDGE_CHECK_INTERVAL_MINUTES = int(os.getenv("NUDGE_CHECK_INTERVAL_MINUTES", "5"))

# Admin API
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "")

# N8N Integration
N8N_ORDER_WEBHOOK_URL = os.getenv("N8N_ORDER_WEBHOOK_URL", "")
ORDER_NOTIFICATION_GROUP_ID = os.getenv("ORDER_NOTIFICATION_GROUP_ID", "")
