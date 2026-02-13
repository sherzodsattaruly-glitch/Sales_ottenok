"""
Парсер экспортов чатов WhatsApp (.txt из .zip).
"""

import os
import re
import logging
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

# Регулярки для разных форматов экспорта WhatsApp
PATTERNS = [
    # [DD.MM.YYYY, HH:MM:SS] Name: Message
    re.compile(r"^\[(\d{2}\.\d{2}\.\d{4}),\s(\d{2}:\d{2}:\d{2})\]\s(.+?):\s(.+)$"),
    # DD/MM/YYYY, HH:MM - Name: Message
    re.compile(r"^(\d{2}/\d{2}/\d{4}),\s(\d{1,2}:\d{2})\s-\s(.+?):\s(.+)$"),
    # DD.MM.YYYY, HH:MM - Name: Message
    re.compile(r"^(\d{2}\.\d{2}\.\d{4}),\s(\d{1,2}:\d{2})\s-\s(.+?):\s(.+)$"),
    # M/D/YY, H:MM AM - Name: Message
    re.compile(r"^(\d{1,2}/\d{1,2}/\d{2,4}),\s(\d{1,2}:\d{2}\s[AP]M)\s-\s(.+?):\s(.+)$"),
]

# Системные сообщения WhatsApp (пропускаем)
SYSTEM_MESSAGES = [
    "Messages and calls are end-to-end encrypted",
    "Сообщения и звонки защищены",
    "created group",
    "added",
    "removed",
    "left",
    "changed the subject",
    "changed this group",
    "<Media omitted>",
    "<Медиа опущено>",
    "изображение отсутствует",
]


def parse_chat_txt(file_path: str) -> list[dict]:
    """Парсит .txt файл экспорта WhatsApp в список сообщений."""
    messages = []
    current_message = None

    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            matched = False

            for pattern in PATTERNS:
                match = pattern.match(line)
                if match:
                    # Сохраняем предыдущее сообщение
                    if current_message and not _is_system_message(current_message["text"]):
                        messages.append(current_message)

                    current_message = {
                        "date": match.group(1),
                        "time": match.group(2),
                        "sender": match.group(3),
                        "text": match.group(4),
                    }
                    matched = True
                    break

            if not matched and current_message:
                # Продолжение многострочного сообщения
                current_message["text"] += "\n" + line

    if current_message and not _is_system_message(current_message["text"]):
        messages.append(current_message)

    logger.info(f"Parsed chat {file_path}: {len(messages)} messages")
    return messages


def extract_chat_from_zip(zip_path: str, output_dir: str) -> str | None:
    """Извлечь WhatsApp-экспорт из ZIP, вернуть путь к .txt файлу."""
    os.makedirs(output_dir, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(output_dir)

    # Найти .txt файл
    for f in os.listdir(output_dir):
        if f.endswith(".txt"):
            return os.path.join(output_dir, f)

    return None


def chat_messages_to_chunks(messages: list[dict], chunk_size: int = 10) -> list[dict]:
    """
    Группирует сообщения в чанки по chunk_size для эмбеддинга.
    Сохраняет контекст разговора.
    """
    chunks = []
    for i in range(0, len(messages), chunk_size):
        batch = messages[i : i + chunk_size]
        text = "\n".join([f"{m['sender']}: {m['text']}" for m in batch])
        chunks.append({
            "text": text,
            "metadata": {
                "source": "whatsapp_chat",
                "type": "chat_example",
                "date_range": f"{batch[0]['date']} - {batch[-1]['date']}",
            },
        })

    return chunks


def _is_system_message(text: str) -> bool:
    """Проверить, является ли сообщение системным."""
    text_lower = text.lower()
    return any(sys_msg.lower() in text_lower for sys_msg in SYSTEM_MESSAGES)
