"""
CLI-инструмент для сборки базы знаний.
Парсит все документы и сохраняет в ChromaDB.

Запуск: python -m knowledge.builder
"""

import os
import glob
import logging

from knowledge.docx_parser import parse_catalog_docx, parse_scripts_docx
from knowledge.chat_parser import parse_chat_txt, extract_chat_from_zip, chat_messages_to_chunks
from knowledge.embeddings import store_in_collection, delete_collection
from config import KNOWLEDGE_BASE_PATH

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def build_knowledge_base():
    """Пересобрать всю базу знаний из исходных документов."""
    logger.info("=== Сборка базы знаний Оттенок ===")

    # 1. Очищаем старые коллекции
    delete_collection("product_catalog")
    delete_collection("sales_scripts")

    # 2. Парсим каталоги товаров (.docx)
    catalog_dir = os.path.join(KNOWLEDGE_BASE_PATH, "catalog")
    all_product_chunks = []

    for docx_file in glob.glob(os.path.join(catalog_dir, "*.docx")):
        logger.info(f"Парсим каталог: {docx_file}")
        chunks = parse_catalog_docx(docx_file)
        all_product_chunks.extend(chunks)

    if all_product_chunks:
        logger.info(f"Сохраняем {len(all_product_chunks)} чанков каталога...")
        store_in_collection("product_catalog", all_product_chunks)
    else:
        logger.warning("Файлы каталога не найдены в data/knowledge_base/catalog/")

    # 3. Парсим скрипты продаж (.docx)
    scripts_dir = os.path.join(KNOWLEDGE_BASE_PATH, "scripts")
    all_script_chunks = []

    for docx_file in glob.glob(os.path.join(scripts_dir, "*.docx")):
        logger.info(f"Парсим скрипт: {docx_file}")
        chunks = parse_scripts_docx(docx_file)
        all_script_chunks.extend(chunks)

    # 4. Парсим экспорты чатов WhatsApp (.zip)
    chats_dir = os.path.join(KNOWLEDGE_BASE_PATH, "chats")

    for zip_file in glob.glob(os.path.join(chats_dir, "*.zip")):
        logger.info(f"Парсим чат (ZIP): {zip_file}")
        extract_dir = zip_file.replace(".zip", "_extracted")
        txt_path = extract_chat_from_zip(zip_file, extract_dir)
        if txt_path:
            messages = parse_chat_txt(txt_path)
            chat_chunks = chat_messages_to_chunks(messages)
            all_script_chunks.extend(chat_chunks)
            logger.info(f"  -> {len(messages)} сообщений, {len(chat_chunks)} чанков")

    # Также обрабатываем .txt файлы напрямую
    for txt_file in glob.glob(os.path.join(chats_dir, "**", "*.txt"), recursive=True):
        # Пропускаем папку "Все" — она дублирует все остальные
        if os.sep + "Все" + os.sep in txt_file or "/Все/" in txt_file:
            continue
        logger.info(f"Парсим чат (TXT): {txt_file}")
        messages = parse_chat_txt(txt_file)
        chat_chunks = chat_messages_to_chunks(messages)
        all_script_chunks.extend(chat_chunks)
        logger.info(f"  -> {len(messages)} сообщений, {len(chat_chunks)} чанков")

    if all_script_chunks:
        logger.info(f"Сохраняем {len(all_script_chunks)} чанков скриптов/чатов...")
        store_in_collection("sales_scripts", all_script_chunks)
    else:
        logger.warning("Файлы скриптов/чатов не найдены")

    logger.info("=== База знаний собрана! ===")


if __name__ == "__main__":
    build_knowledge_base()
