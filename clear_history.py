"""
Скрипт для очистки истории диалогов бота.
Удаляет все записи из таблицы conversations.
"""

import sqlite3
from config import SQLITE_DB_PATH

def clear_conversation_history():
    """Очистить всю историю диалогов."""
    conn = sqlite3.connect(SQLITE_DB_PATH)
    cursor = conn.cursor()
    
    # Получаем количество записей до удаления
    cursor.execute("SELECT COUNT(*) FROM conversations")
    count_before = cursor.fetchone()[0]
    
    # Удаляем все записи из таблицы conversations
    cursor.execute("DELETE FROM conversations")
    
    # Сбрасываем автоинкремент
    cursor.execute("DELETE FROM sqlite_sequence WHERE name='conversations'")
    
    conn.commit()
    
    # Проверяем количество записей после удаления
    cursor.execute("SELECT COUNT(*) FROM conversations")
    count_after = cursor.fetchone()[0]
    
    conn.close()
    
    print(f"Conversation history cleared!")
    print(f"   Records deleted: {count_before}")
    print(f"   Records remaining: {count_after}")

if __name__ == "__main__":
    print("Clearing conversation history...")
    clear_conversation_history()
