"""N8N webhook integration for order notifications."""
import logging
import httpx
from config import N8N_ORDER_WEBHOOK_URL

logger = logging.getLogger(__name__)


async def notify_order_confirmed(chat_id: str, order_ctx: dict, sender_name: str = "") -> None:
    """Fire-and-forget webhook to N8N when order is confirmed."""
    if not N8N_ORDER_WEBHOOK_URL:
        return
    payload = {
        "event": "order_confirmed",
        "chat_id": chat_id,
        "sender_name": sender_name,
        "product": order_ctx.get("product", ""),
        "size": order_ctx.get("size", ""),
        "color": order_ctx.get("color", ""),
        "city": order_ctx.get("city", ""),
        "address": order_ctx.get("address", ""),
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(N8N_ORDER_WEBHOOK_URL, json=payload)
    except Exception as e:
        logger.warning(f"Failed to notify N8N: {e}")
