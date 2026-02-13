"""
Telegram error notification with throttling.
Sends alerts to owner when bot encounters errors.
"""
import logging
import time

import httpx

from config import TELEGRAM_ALERT_BOT_TOKEN, TELEGRAM_ALERT_CHAT_ID

logger = logging.getLogger(__name__)

_last_sent: dict[str, float] = {}
_THROTTLE_SECONDS = 600  # 10 minutes per error type


async def notify_error(error_type: str, message: str) -> None:
    """Send error alert to Telegram. Throttled to 1 per error_type per 10 min."""
    if not TELEGRAM_ALERT_BOT_TOKEN or not TELEGRAM_ALERT_CHAT_ID:
        return
    now = time.time()
    if now - _last_sent.get(error_type, 0) < _THROTTLE_SECONDS:
        return
    _last_sent[error_type] = now
    text = f"⚠️ Sales Ottenok Error\n\nType: {error_type}\n{message[:1000]}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_ALERT_BOT_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_ALERT_CHAT_ID, "text": text},
            )
    except Exception as e:
        logger.warning(f"Failed to send Telegram alert: {e}")
