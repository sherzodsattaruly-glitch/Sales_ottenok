"""
Sales Ottenok WhatsApp Bot — точка входа.
FastAPI webhook-сервер + автоматический дожим клиентов.
"""

import logging
from logging.handlers import RotatingFileHandler
import asyncio
import time as _time
from contextlib import asynccontextmanager

import aiosqlite
import uvicorn
from fastapi import FastAPI

from config import WEBHOOK_HOST, WEBHOOK_PORT, GREEN_API_POLLING, GREEN_API_POLL_INTERVAL, SQLITE_DB_PATH
from greenapi.webhook import router as webhook_router, set_message_handler
from greenapi.poller import poll_notifications
from gdrive.photo_mapper import load_photo_index
from db.models import init_db
from ai.engine import handle_message
from scheduler.nudge_scheduler import get_nudge_scheduler
from admin.routes import router as admin_router

# Логирование
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler(
            "data/sales_ottenok.log",
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=5,
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Инициализация при старте, очистка при остановке."""
    logger.info("Запуск бота Sales Ottenok...")
    init_db()
    load_photo_index()
    set_message_handler(handle_message)

    poll_task = None
    if GREEN_API_POLLING:
        poll_task = asyncio.create_task(poll_notifications(GREEN_API_POLL_INTERVAL))
        logger.info("Green API polling enabled.")

    # Запускаем scheduler для автоматического дожима
    nudge_scheduler = get_nudge_scheduler()
    nudge_scheduler.start()
    logger.info("Nudge scheduler started.")

    logger.info("Бот готов к работе!")
    yield

    # Cleanup
    if poll_task:
        poll_task.cancel()

    # Останавливаем scheduler
    nudge_scheduler.shutdown()
    logger.info("Nudge scheduler stopped.")

    logger.info("Бот остановлен.")


app = FastAPI(title="Sales Ottenok Bot", lifespan=lifespan)
app.include_router(webhook_router)
app.include_router(admin_router)

_start_time = _time.time()


@app.get("/health")
async def health():
    checks = {"uptime_seconds": int(_time.time() - _start_time)}

    # Check SQLite
    try:
        async with aiosqlite.connect(SQLITE_DB_PATH) as db:
            await db.execute("SELECT 1")
        checks["db"] = "ok"
    except Exception as e:
        checks["db"] = f"error: {e}"

    # Check photo index
    from gdrive.photo_mapper import _photo_index
    checks["photo_index_products"] = len(_photo_index) if _photo_index else 0

    # Check ChromaDB
    try:
        from ai.rag import chroma_client
        collections = chroma_client.list_collections()
        checks["chromadb_collections"] = len(collections)
    except Exception as e:
        checks["chromadb_collections"] = f"error: {e}"

    all_ok = checks.get("db") == "ok" and checks.get("photo_index_products", 0) > 0
    checks["status"] = "ok" if all_ok else "degraded"

    return checks


if __name__ == "__main__":
    uvicorn.run("main:app", host=WEBHOOK_HOST, port=WEBHOOK_PORT, reload=False)
