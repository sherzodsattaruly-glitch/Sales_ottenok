"""
Admin endpoints for conversation review.
Protected by ADMIN_API_KEY header.
"""
import logging

from fastapi import APIRouter, Header, HTTPException
import aiosqlite

from config import SQLITE_DB_PATH, ADMIN_API_KEY

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])


async def _verify_key(x_api_key: str = Header(...)):
    if not ADMIN_API_KEY or x_api_key != ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


@router.get("/conversations")
async def list_conversations(x_api_key: str = Header(...), limit: int = 50):
    await _verify_key(x_api_key)
    async with aiosqlite.connect(SQLITE_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT chat_id, name, last_message_at, message_count,
                      last_client_text, nudge_count, nudge_state
               FROM clients ORDER BY last_message_at DESC LIMIT ?""",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


@router.get("/conversations/{chat_id}")
async def get_conversation(chat_id: str, x_api_key: str = Header(...), limit: int = 100):
    await _verify_key(x_api_key)
    async with aiosqlite.connect(SQLITE_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT role, content, sender_name, created_at
               FROM conversations WHERE chat_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (chat_id, limit),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in reversed(rows)]


@router.get("/orders/{chat_id}")
async def get_order_context(chat_id: str, x_api_key: str = Header(...)):
    await _verify_key(x_api_key)
    async with aiosqlite.connect(SQLITE_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT * FROM client_order_context WHERE chat_id = ?""",
            (chat_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else {}
