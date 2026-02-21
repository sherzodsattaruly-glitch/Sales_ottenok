"""SQLite — история, состояние клиентов, заказы."""

from __future__ import annotations

import sqlite3
import json
import aiosqlite
from config import SQLITE_DB_PATH, MAX_CONVERSATION_HISTORY


def init_db():
    conn = sqlite3.connect(SQLITE_DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            sender_name TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_conv_chat ON conversations(chat_id)")

    c.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            chat_id TEXT PRIMARY KEY,
            name TEXT DEFAULT '',
            last_client_message_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_bot_message_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_client_text TEXT DEFAULT '',
            nudge_count INTEGER DEFAULT 0,
            last_nudge_at TIMESTAMP,
            handoff INTEGER DEFAULT 0,
            order_state TEXT DEFAULT '{}',
            message_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS sent_photos (
            chat_id TEXT NOT NULL,
            product_key TEXT NOT NULL,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (chat_id, product_key)
        )
    """)

    # Migration: add client_status column
    try:
        c.execute("ALTER TABLE clients ADD COLUMN client_status TEXT DEFAULT 'active'")
        c.execute("UPDATE clients SET client_status = 'active' WHERE client_status IS NULL")
    except sqlite3.OperationalError:
        pass  # Column already exists

    conn.commit()
    conn.close()


async def save_message(chat_id: str, role: str, content: str, sender_name: str = ""):
    async with aiosqlite.connect(SQLITE_DB_PATH) as db:
        await db.execute(
            "INSERT INTO conversations (chat_id, role, content, sender_name) VALUES (?, ?, ?, ?)",
            (chat_id, role, content, sender_name),
        )
        if role == "user":
            await db.execute("""
                INSERT INTO clients (chat_id, name, last_client_message_at, last_client_text, message_count)
                VALUES (?, ?, CURRENT_TIMESTAMP, ?, 1)
                ON CONFLICT(chat_id) DO UPDATE SET
                    last_client_message_at = CURRENT_TIMESTAMP,
                    last_client_text = ?,
                    message_count = message_count + 1,
                    name = CASE WHEN ? != '' THEN ? ELSE name END
            """, (chat_id, sender_name, content, content, sender_name, sender_name))
        else:
            await db.execute("""
                INSERT INTO clients (chat_id, name, last_bot_message_at, message_count)
                VALUES (?, ?, CURRENT_TIMESTAMP, 1)
                ON CONFLICT(chat_id) DO UPDATE SET
                    last_bot_message_at = CURRENT_TIMESTAMP,
                    message_count = message_count + 1
            """, (chat_id, sender_name))
        await db.commit()


async def get_history(chat_id: str, limit: int = MAX_CONVERSATION_HISTORY) -> list[dict]:
    async with aiosqlite.connect(SQLITE_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT role, content FROM conversations WHERE chat_id = ? ORDER BY created_at DESC LIMIT ?",
            (chat_id, limit),
        )
        rows = await cur.fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


async def get_client(chat_id: str) -> dict | None:
    async with aiosqlite.connect(SQLITE_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM clients WHERE chat_id = ?", (chat_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def update_client(chat_id: str, **kwargs):
    if not kwargs:
        return
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [chat_id]
    async with aiosqlite.connect(SQLITE_DB_PATH) as db:
        await db.execute(f"UPDATE clients SET {sets} WHERE chat_id = ?", vals)
        await db.commit()


async def get_order_state(chat_id: str) -> dict:
    client = await get_client(chat_id)
    if not client:
        return {}
    try:
        return json.loads(client.get("order_state") or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}


async def save_order_state(chat_id: str, state: dict):
    await update_client(chat_id, order_state=json.dumps(state, ensure_ascii=False))


async def is_handoff(chat_id: str) -> bool:
    client = await get_client(chat_id)
    return bool(client and client.get("handoff"))


async def set_handoff(chat_id: str, enabled: bool):
    async with aiosqlite.connect(SQLITE_DB_PATH) as db:
        await db.execute("""
            INSERT INTO clients (chat_id, handoff) VALUES (?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET handoff = ?
        """, (chat_id, int(enabled), int(enabled)))
        await db.commit()


async def mark_photos_sent(chat_id: str, file_ids: list[str]):
    """Пометить file_id как отправленные клиенту."""
    async with aiosqlite.connect(SQLITE_DB_PATH) as db:
        await db.executemany(
            "INSERT OR IGNORE INTO sent_photos (chat_id, product_key) VALUES (?, ?)",
            [(chat_id, fid) for fid in file_ids],
        )
        await db.commit()


async def get_sent_photo_ids(chat_id: str) -> set[str]:
    """Получить все file_id, которые уже отправлялись клиенту."""
    async with aiosqlite.connect(SQLITE_DB_PATH) as db:
        cur = await db.execute(
            "SELECT product_key FROM sent_photos WHERE chat_id = ?",
            (chat_id,),
        )
        rows = await cur.fetchall()
        return {r[0] for r in rows}


async def set_client_status(chat_id: str, status: str):
    """Set client lifecycle status: active | ordered | fitting | declined."""
    valid = {"active", "ordered", "fitting", "declined"}
    if status not in valid:
        raise ValueError(f"Invalid client_status: {status!r}. Must be one of {valid}")
    await update_client(chat_id, client_status=status)


async def get_recent_messages(chat_id: str, limit: int = 6) -> list[dict]:
    """Последние N сообщений для LLM-классификатора."""
    async with aiosqlite.connect(SQLITE_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT role, content FROM conversations WHERE chat_id = ? ORDER BY created_at DESC LIMIT ?",
            (chat_id, limit),
        )
        rows = await cur.fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


async def get_nudge_candidates() -> list[dict]:
    """Клиенты, которым потенциально нужен дожим."""
    async with aiosqlite.connect(SQLITE_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("""
            SELECT * FROM clients
            WHERE handoff = 0
              AND nudge_count < 2
              AND last_bot_message_at > last_client_message_at
              AND client_status = 'active'
        """)
        return [dict(r) for r in await cur.fetchall()]
