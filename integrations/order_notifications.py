"""WhatsApp group notification when order is confirmed."""
import logging

from config import ORDER_NOTIFICATION_GROUP_ID
from greenapi.client import send_contact, send_text

logger = logging.getLogger(__name__)


async def notify_order_to_group(chat_id: str, order_ctx: dict, sender_name: str = "") -> None:
    """Send contact card + order details to the manager WhatsApp group."""
    if not ORDER_NOTIFICATION_GROUP_ID:
        return

    # Send contact card
    phone_str = chat_id.replace("@c.us", "")
    try:
        phone_int = int(phone_str)
        try:
            await send_contact(
                ORDER_NOTIFICATION_GROUP_ID,
                phone_int,
                sender_name or "Клиент",
            )
        except Exception as e:
            logger.error(f"Failed to send contact card to group: {e}")
    except (ValueError, TypeError):
        logger.warning(f"Could not parse phone from chat_id={chat_id}, skipping contact card")

    # Build order details text
    product = order_ctx.get("product", "")
    size = order_ctx.get("size", "")
    color = order_ctx.get("color", "")
    city = order_ctx.get("city", "")
    address = order_ctx.get("address", "")

    display_name = sender_name or phone_str
    lines = [f"Новый заказ от {display_name}:"]
    if product:
        lines.append(f"Товар: {product}")
    if size:
        lines.append(f"Размер: {size}")
    if color:
        lines.append(f"Цвет: {color}")
    if city:
        lines.append(f"Город: {city}")
    if address:
        lines.append(f"Адрес: {address}")

    try:
        await send_text(ORDER_NOTIFICATION_GROUP_ID, "\n".join(lines))
    except Exception as e:
        logger.error(f"Failed to send order details to group: {e}")
