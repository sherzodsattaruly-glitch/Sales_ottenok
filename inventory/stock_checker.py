"""
Проверка наличия товаров в Excel.
Использует токенизацию для поиска товаров по названию.
"""

import logging
from typing import Dict, List

import pandas as pd

from gdrive.photo_mapper import tokenize_text
from .excel_loader import get_inventory_df

logger = logging.getLogger(__name__)


def check_product_availability(
    product_name: str,
    size: str = "",
    color: str = ""
) -> Dict:
    """
    Проверить наличие товара в Excel файле.

    Args:
        product_name: Название товара (например, "Chanel Jumbo")
        size: Размер (опционально, например "38")
        color: Цвет (опционально, например "розовые")

    Returns:
        dict с полями:
            - available: bool - есть ли товар в наличии
            - matches: list - список найденных совпадений (дикты с полями из Excel)
            - quantity: int - общее количество (сумма по всем совпадениям)
            - price: str - цена (из первого совпадения)
    """
    df = get_inventory_df()

    if df.empty:
        logger.warning("Inventory DataFrame пустой")
        return {
            "available": False,
            "matches": [],
            "quantity": 0,
            "price": ""
        }

    # Токенизируем название товара
    query_tokens = tokenize_text(product_name)

    if not query_tokens:
        logger.warning("Не удалось токенизировать product_name: %s", product_name)
        return {
            "available": False,
            "matches": [],
            "quantity": 0,
            "price": ""
        }

    # Ищем совпадения
    matches = []

    for idx, row in df.iterrows():
        row_product_name = str(row.get("product_name", ""))
        row_size = str(row.get("size", "")).strip()
        row_color = str(row.get("color", "")).strip().lower()
        row_quantity = int(row.get("quantity", 0))
        row_price = str(row.get("price", ""))

        # Токенизируем название товара из Excel
        product_tokens = tokenize_text(row_product_name)

        # Подсчитываем количество совпавших токенов
        overlap = query_tokens & product_tokens
        overlap_score = len(overlap)

        # Требуем минимум 2 совпавших токена для релевантности
        if overlap_score < 2:
            continue

        # Фильтрация по размеру (если задан)
        if size and size.strip():
            # Проверяем точное совпадение размера
            if row_size != size.strip():
                continue

        # Фильтрация по цвету (если задан)
        if color and color.strip():
            # Проверяем вхождение цвета (например, "розов" в "розовые")
            color_lower = color.strip().lower()
            if color_lower not in row_color and row_color not in color_lower:
                continue

        # Добавляем совпадение
        matches.append({
            "product_name": row_product_name,
            "size": row_size,
            "color": row_color,
            "quantity": row_quantity,
            "price": row_price,
            "overlap_score": overlap_score
        })

    # Сортируем по overlap_score (больше совпадений = выше в списке)
    matches.sort(key=lambda x: x["overlap_score"], reverse=True)

    # Подсчитываем общее количество
    total_quantity = sum(m["quantity"] for m in matches)

    # Товар доступен, если есть хотя бы одно совпадение с quantity > 0
    available = any(m["quantity"] > 0 for m in matches)

    # Берем цену из первого совпадения
    price = matches[0]["price"] if matches else ""

    return {
        "available": available,
        "matches": matches,
        "quantity": total_quantity,
        "price": price
    }


def format_availability_message(availability: Dict, product_name: str) -> str:
    """
    Форматировать сообщение о наличии/отсутствии товара.

    Args:
        availability: Результат check_product_availability()
        product_name: Название товара

    Returns:
        Текст сообщения для клиента
    """
    if availability["available"]:
        # Товар есть в наличии
        price = availability["price"]
        if price:
            return f"Да, {product_name} есть в наличии! Цена: {price}."
        else:
            return f"Да, {product_name} есть в наличии!"
    else:
        # Проверяем, найдены ли совпадения (но нет в наличии)
        if availability["matches"]:
            return f"К сожалению, {product_name} сейчас нет в наличии. Ожидаем поступление в ближайшее время."
        else:
            # Товар вообще не найден
            return f"По модели {product_name} сейчас не вижу в наличии. Могу подобрать похожий вариант?"
