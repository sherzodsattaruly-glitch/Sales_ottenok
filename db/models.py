"""
SQLite схема базы данных.
"""

import sqlite3
from config import SQLITE_DB_PATH


def init_db():
    """Создать таблицы, если не существуют."""
    conn = sqlite3.connect(SQLITE_DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            sender_name TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_conversations_chat_id
        ON conversations(chat_id)
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            chat_id TEXT PRIMARY KEY,
            name TEXT,
            phone TEXT,
            first_message_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_message_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            message_count INTEGER DEFAULT 0,

            -- Новые поля для дожима
            last_client_message_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_bot_message_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            nudge_count INTEGER DEFAULT 0,
            last_nudge_at TIMESTAMP,
            nudge_state TEXT DEFAULT 'pending',
            last_client_text TEXT DEFAULT ''
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sent_photos (
            chat_id TEXT NOT NULL,
            product_key TEXT NOT NULL,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (chat_id, product_key)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS handoff_state (
            chat_id TEXT PRIMARY KEY,
            enabled INTEGER DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS client_order_context (
            chat_id TEXT PRIMARY KEY,
            city TEXT DEFAULT '',
            product TEXT DEFAULT '',
            product_type TEXT DEFAULT '',
            size TEXT DEFAULT '',
            color TEXT DEFAULT '',
            address TEXT DEFAULT '',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Миграция: добавляем новые поля если они отсутствуют (для существующих БД)
    _add_column_if_not_exists(cursor, "clients", "last_client_message_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    _add_column_if_not_exists(cursor, "clients", "last_bot_message_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    _add_column_if_not_exists(cursor, "clients", "nudge_count", "INTEGER DEFAULT 0")
    _add_column_if_not_exists(cursor, "clients", "last_nudge_at", "TIMESTAMP")
    _add_column_if_not_exists(cursor, "clients", "nudge_state", "TEXT DEFAULT 'pending'")
    _add_column_if_not_exists(cursor, "clients", "last_client_text", "TEXT DEFAULT ''")

    conn.commit()
    conn.close()


def _add_column_if_not_exists(cursor, table: str, column: str, column_type: str):
    """Добавить колонку если она не существует."""
    # Получаем список существующих колонок
    cursor.execute(f"PRAGMA table_info({table})")
    columns = [row[1] for row in cursor.fetchall()]

    if column not in columns:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
        print(f"Added column {column} to {table}")
