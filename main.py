"""
Sales Ottenok WhatsApp Bot — точка входа.
FastAPI webhook-сервер + автоматический дожим клиентов.
"""

import logging
import asyncio
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from config import WEBHOOK_HOST, WEBHOOK_PORT, GREEN_API_POLLING, GREEN_API_POLL_INTERVAL
from greenapi.webhook import router as webhook_router, set_message_handler
from greenapi.poller import poll_notifications
from gdrive.photo_mapper import load_photo_index
from db.models import init_db
from ai.engine import handle_message
from scheduler.nudge_scheduler import get_nudge_scheduler

# Логирование
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("data/sales_ottenok.log", encoding="utf-8"),
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


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run("main:app", host=WEBHOOK_HOST, port=WEBHOOK_PORT, reload=False)
