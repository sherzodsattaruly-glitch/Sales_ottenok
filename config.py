"""Конфигурация бота Оттенок v2."""

import os
import re
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# Green API
GREEN_API_INSTANCE_ID = os.getenv("GREEN_API_INSTANCE_ID", "")
GREEN_API_TOKEN = os.getenv("GREEN_API_TOKEN", "")
GREEN_API_POLLING = os.getenv("GREEN_API_POLLING", "1").lower() in ("1", "true", "yes")
GREEN_API_POLL_INTERVAL = float(os.getenv("GREEN_API_POLL_INTERVAL", "2.0"))

# OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

# Google
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials/google_credentials.json")
GOOGLE_DRIVE_PHOTOS_FOLDER_ID = os.getenv("GOOGLE_DRIVE_PHOTOS_FOLDER_ID", "")
CATALOG_SHEETS_ID = os.getenv("CATALOG_SHEETS_ID", "")

# DB
SQLITE_DB_PATH = os.getenv("SQLITE_DB_PATH", "data/ottenok.db")

# Server
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "0.0.0.0")
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8080"))

# Bot behavior
MAX_CONVERSATION_HISTORY = int(os.getenv("MAX_CONVERSATION_HISTORY", "20"))
MESSAGE_AGGREGATION_DELAY = float(os.getenv("MESSAGE_AGGREGATION_DELAY", "3.0"))

# OpenAI Agents SDK
OPENAI_TEMPERATURE = float(os.getenv("OPENAI_TEMPERATURE", "0.4"))
OPENAI_MAX_TOKENS = int(os.getenv("OPENAI_MAX_TOKENS", "1500"))
AGENT_MAX_TURNS = int(os.getenv("AGENT_MAX_TURNS", "5"))
AGENT_SESSIONS_DB_PATH = os.getenv("AGENT_SESSIONS_DB_PATH", "data/agent_sessions.db")

# Manager
def _normalize_chat_id(value: str) -> str:
    v = (value or "").strip()
    if not v:
        return ""
    if "@c.us" in v:
        return v
    digits = re.sub(r"\D", "", v)
    return f"{digits}@c.us" if digits else ""

_manager_raw = os.getenv("MANAGER_NUMBERS", "")
MANAGER_CHAT_IDS = {cid for cid in (_normalize_chat_id(x) for x in _manager_raw.split(",")) if cid}

# Notifications
TELEGRAM_ALERT_BOT_TOKEN = os.getenv("TELEGRAM_ALERT_BOT_TOKEN", "")
TELEGRAM_ALERT_CHAT_ID = os.getenv("TELEGRAM_ALERT_CHAT_ID", "")

# WhatsApp group for order notifications (format: 120363XXXXXXXXXX@g.us)
ORDER_GROUP_CHAT_ID = os.getenv("ORDER_GROUP_CHAT_ID", "")

# N8N
N8N_ORDER_WEBHOOK_URL = os.getenv("N8N_ORDER_WEBHOOK_URL", "")

# Nudge
NUDGE_ENABLED = os.getenv("NUDGE_ENABLED", "1").lower() in ("1", "true", "yes")
NUDGE_CHECK_INTERVAL_MINUTES = int(os.getenv("NUDGE_CHECK_INTERVAL_MINUTES", "5"))
