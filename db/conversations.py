"""
CRUD операции для истории переписки.
"""

import aiosqlite
from config import SQLITE_DB_PATH, MAX_CONVERSATION_HISTORY


async def save_message(chat_id: str, role: str, content: str, sender_name: str = ""):
    """Сохранить сообщение в историю."""
    async with aiosqlite.connect(SQLITE_DB_PATH) as db:
        await db.execute(
            "INSERT INTO conversations (chat_id, role, content, sender_name) VALUES (?, ?, ?, ?)",
            (chat_id, role, content, sender_name),
        )

        # Обновляем timestamps в зависимости от роли
        if role == "user":
            # Сообщение от клиента
            await db.execute(
                """
                INSERT INTO clients (chat_id, name, last_message_at, message_count, last_client_message_at, last_client_text)
                VALUES (?, ?, CURRENT_TIMESTAMP, 1, CURRENT_TIMESTAMP, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    last_message_at = CURRENT_TIMESTAMP,
                    message_count = message_count + 1,
                    name = CASE WHEN ? != '' THEN ? ELSE name END,
                    last_client_message_at = CURRENT_TIMESTAMP,
                    last_client_text = ?
                """,
                (chat_id, sender_name, content, sender_name, sender_name, content),
            )
        else:
            # Сообщение от бота
            await db.execute(
                """
                INSERT INTO clients (chat_id, name, last_message_at, message_count, last_bot_message_at)
                VALUES (?, ?, CURRENT_TIMESTAMP, 1, CURRENT_TIMESTAMP)
                ON CONFLICT(chat_id) DO UPDATE SET
                    last_message_at = CURRENT_TIMESTAMP,
                    message_count = message_count + 1,
                    name = CASE WHEN ? != '' THEN ? ELSE name END,
                    last_bot_message_at = CURRENT_TIMESTAMP
                """,
                (chat_id, sender_name, sender_name, sender_name),
            )

        await db.commit()


async def get_conversation_history(chat_id: str, limit: int = MAX_CONVERSATION_HISTORY) -> list[dict]:
    """Получить последние сообщения клиента."""
    async with aiosqlite.connect(SQLITE_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT role, content, created_at
               FROM conversations
               WHERE chat_id = ?
               ORDER BY created_at DESC
               LIMIT ?""",
            (chat_id, limit),
        )
        rows = await cursor.fetchall()
        return [
            {"role": r["role"], "content": r["content"], "created_at": r["created_at"]}
            for r in reversed(rows)
        ]

async def get_client_message_count(chat_id: str) -> int:
    """Получить количество сообщений по клиенту (включая ответы бота)."""
    async with aiosqlite.connect(SQLITE_DB_PATH) as db:
        cursor = await db.execute(
            "SELECT message_count FROM clients WHERE chat_id = ?",
            (chat_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return 0
        return int(row[0] or 0)


async def has_sent_product_photos(chat_id: str, product_key: str) -> bool:
    """Проверить, отправлялись ли фото товара этому клиенту."""
    if not product_key:
        return False
    async with aiosqlite.connect(SQLITE_DB_PATH) as db:
        cursor = await db.execute(
            "SELECT 1 FROM sent_photos WHERE chat_id = ? AND product_key = ?",
            (chat_id, product_key),
        )
        row = await cursor.fetchone()
        return row is not None


async def has_any_sent_photos(chat_id: str) -> bool:
    """Проверить, отправлялись ли клиенту какие-либо фото ранее."""
    async with aiosqlite.connect(SQLITE_DB_PATH) as db:
        cursor = await db.execute(
            "SELECT 1 FROM sent_photos WHERE chat_id = ? LIMIT 1",
            (chat_id,),
        )
        row = await cursor.fetchone()
        return row is not None


async def mark_product_photos_sent(chat_id: str, product_key: str) -> None:
    """Отметить, что фото товара отправлены клиенту."""
    if not product_key:
        return
    async with aiosqlite.connect(SQLITE_DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO sent_photos (chat_id, product_key) VALUES (?, ?)",
            (chat_id, product_key),
        )
        await db.commit()


async def get_handoff_state(chat_id: str) -> bool:
    """Проверить, включена ли передача менеджеру по чату."""
    async with aiosqlite.connect(SQLITE_DB_PATH) as db:
        cursor = await db.execute(
            "SELECT enabled FROM handoff_state WHERE chat_id = ?",
            (chat_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return False
        return bool(row[0])


async def set_handoff_state(chat_id: str, enabled: bool) -> None:
    """Включить/выключить передачу менеджеру по чату."""
    async with aiosqlite.connect(SQLITE_DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO handoff_state (chat_id, enabled, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(chat_id) DO UPDATE SET
                enabled = excluded.enabled,
                updated_at = CURRENT_TIMESTAMP
            """,
            (chat_id, 1 if enabled else 0),
        )
        await db.commit()


async def get_order_context(chat_id: str) -> dict:
    """Получить сохраненный контекст заказа клиента."""
    async with aiosqlite.connect(SQLITE_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT city, product, product_type, size, color, address
            FROM client_order_context
            WHERE chat_id = ?
            """,
            (chat_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return {
                "city": "",
                "product": "",
                "product_type": "",
                "size": "",
                "color": "",
                "address": "",
            }
        return {
            "city": row["city"] or "",
            "product": row["product"] or "",
            "product_type": row["product_type"] or "",
            "size": row["size"] or "",
            "color": row["color"] or "",
            "address": row["address"] or "",
        }


async def upsert_order_context(chat_id: str, fields: dict) -> None:
    """Сохранить контекст заказа клиента."""
    async with aiosqlite.connect(SQLITE_DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO client_order_context
            (chat_id, city, product, product_type, size, color, address, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(chat_id) DO UPDATE SET
                city = excluded.city,
                product = excluded.product,
                product_type = excluded.product_type,
                size = excluded.size,
                color = excluded.color,
                address = excluded.address,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                chat_id,
                (fields.get("city") or "").strip(),
                (fields.get("product") or "").strip(),
                (fields.get("product_type") or "").strip(),
                (fields.get("size") or "").strip(),
                (fields.get("color") or "").strip(),
                (fields.get("address") or "").strip(),
            ),
        )
        await db.commit()


async def get_order_pending_confirm(chat_id: str) -> bool:
    """Проверить, ожидает ли заказ подтверждения от клиента."""
    async with aiosqlite.connect(SQLITE_DB_PATH) as db:
        cursor = await db.execute(
            "SELECT order_pending_confirm FROM client_order_context WHERE chat_id = ?",
            (chat_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return False
        return bool(row[0])


async def set_order_pending_confirm(chat_id: str, pending: bool) -> None:
    """Установить/сбросить флаг ожидания подтверждения заказа."""
    async with aiosqlite.connect(SQLITE_DB_PATH) as db:
        await db.execute(
            """
            UPDATE client_order_context
            SET order_pending_confirm = ?
            WHERE chat_id = ?
            """,
            (1 if pending else 0, chat_id),
        )
        await db.commit()


async def clear_old_conversations(days: int = 30):
    """Удалить переписки старше N дней."""
    async with aiosqlite.connect(SQLITE_DB_PATH) as db:
        await db.execute(
            "DELETE FROM conversations WHERE created_at < datetime('now', ?)",
            (f"-{days} days",),
        )
        await db.commit()


# ============================================================================
# Функции для системы автоматического дожима
# ============================================================================

async def get_clients_for_nudge() -> list[dict]:
    """
    Получить список клиентов для потенциального дожима.

    Returns:
        Список словарей с данными клиентов
    """
    async with aiosqlite.connect(SQLITE_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT
                c.chat_id,
                c.last_client_message_at,
                c.last_bot_message_at,
                c.nudge_count,
                c.last_client_text,
                COALESCE(h.enabled, 0) as handoff_enabled
            FROM clients c
            LEFT JOIN handoff_state h ON c.chat_id = h.chat_id
            WHERE c.nudge_count < 2
              AND COALESCE(c.nudge_state, 'pending') != 'stopped'
            ORDER BY c.last_client_message_at DESC
            """,
        )
        rows = await cursor.fetchall()
        return [
            {
                "chat_id": r["chat_id"],
                "last_client_message_at": r["last_client_message_at"],
                "last_bot_message_at": r["last_bot_message_at"],
                "nudge_count": r["nudge_count"] or 0,
                "last_client_text": r["last_client_text"] or "",
                "handoff_enabled": bool(r["handoff_enabled"]),
            }
            for r in rows
        ]


async def mark_nudge_sent(chat_id: str, new_nudge_count: int) -> None:
    """
    Отметить отправку дожима.

    Args:
        chat_id: ID чата
        new_nudge_count: Новое значение счетчика дожимов
    """
    async with aiosqlite.connect(SQLITE_DB_PATH) as db:
        nudge_state = "completed" if new_nudge_count >= 2 else "in_progress"

        await db.execute(
            """
            UPDATE clients
            SET nudge_count = ?,
                last_nudge_at = CURRENT_TIMESTAMP,
                last_bot_message_at = CURRENT_TIMESTAMP,
                nudge_state = ?
            WHERE chat_id = ?
            """,
            (new_nudge_count, nudge_state, chat_id),
        )
        await db.commit()


async def reset_nudge_state(chat_id: str) -> None:
    """
    Сбросить состояние дожима (когда клиент ответил).

    Args:
        chat_id: ID чата
    """
    async with aiosqlite.connect(SQLITE_DB_PATH) as db:
        await db.execute(
            """
            UPDATE clients
            SET nudge_count = 0,
                nudge_state = 'pending'
            WHERE chat_id = ?
            """,
            (chat_id,),
        )
        await db.commit()


async def stop_nudging(chat_id: str) -> None:
    """
    Остановить дожим навсегда (по команде менеджера).

    Args:
        chat_id: ID чата
    """
    async with aiosqlite.connect(SQLITE_DB_PATH) as db:
        await db.execute(
            """
            UPDATE clients
            SET nudge_state = 'stopped'
            WHERE chat_id = ?
            """,
            (chat_id,),
        )
        await db.commit()


async def update_last_client_message(chat_id: str, text: str) -> None:
    """
    Обновить время и текст последнего сообщения клиента.

    Args:
        chat_id: ID чата
        text: Текст сообщения
    """
    async with aiosqlite.connect(SQLITE_DB_PATH) as db:
        await db.execute(
            """
            UPDATE clients
            SET last_client_message_at = CURRENT_TIMESTAMP,
                last_client_text = ?
            WHERE chat_id = ?
            """,
            (text, chat_id),
        )
        await db.commit()


# Алиас для совместимости с scheduler
async def get_client_order_context(chat_id: str) -> dict:
    """
    Получить контекст заказа клиента.
    Алиас для get_order_context().

    Args:
        chat_id: ID чата

    Returns:
        Словарь с контекстом заказа
    """
    return await get_order_context(chat_id)
