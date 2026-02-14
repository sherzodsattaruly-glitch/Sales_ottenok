"""
Загрузка каталога товаров из Google Sheets с кэшированием.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from config import CATALOG_SHEETS_ID, CATALOG_CACHE_TTL
from gdrive.sheets_client import read_catalog_from_sheets

logger = logging.getLogger(__name__)


class CatalogLoader:
    """Загрузчик каталога из Google Sheets с кэшированием."""

    def __init__(self):
        self._cache: Optional[list[dict]] = None
        self._cache_time: Optional[datetime] = None
        self._cache_ttl = timedelta(seconds=CATALOG_CACHE_TTL)

    def load_catalog(self, force_reload: bool = False) -> list[dict]:
        """
        Загрузить каталог товаров из Google Sheets.

        Args:
            force_reload: Принудительная перезагрузка (игнорировать кэш)

        Returns:
            Список словарей с товарами. Каждый товар содержит:
            - name: название товара
            - category: категория
            - price: цена
            - colors: доступные цвета
            - descriptions: описание
        """
        now = datetime.now()

        # Проверяем кэш
        if not force_reload and self._cache is not None and self._cache_time is not None:
            if now - self._cache_time < self._cache_ttl:
                logger.debug(f"Используем кэшированный catalog (age: {now - self._cache_time})")
                return self._cache

        # Загружаем из Google Sheets
        try:
            if not CATALOG_SHEETS_ID:
                logger.warning("CATALOG_SHEETS_ID не задан в .env")
                return []

            logger.info(f"Загружаем catalog из Google Sheets {CATALOG_SHEETS_ID}")
            products = read_catalog_from_sheets(CATALOG_SHEETS_ID)

            # Обновляем кэш
            self._cache = products
            self._cache_time = now

            logger.info(f"Загружено {len(products)} товаров из Google Sheets")
            return products

        except Exception as e:
            logger.error(f"Ошибка загрузки catalog из Google Sheets: {e}", exc_info=True)
            # В случае ошибки возвращаем пустой список (или старый кэш если есть)
            if self._cache is not None:
                logger.warning("Возвращаем старый кэш из-за ошибки загрузки")
                return self._cache
            return []


# Singleton instance
_catalog_loader = CatalogLoader()


def get_catalog(force_reload: bool = False) -> list[dict]:
    """
    Получить каталог товаров (с кэшированием).

    Args:
        force_reload: Принудительная перезагрузка из Google Sheets

    Returns:
        Список словарей с товарами
    """
    return _catalog_loader.load_catalog(force_reload=force_reload)


def reload_catalog() -> list[dict]:
    """
    Принудительно перезагрузить catalog из Google Sheets.

    Returns:
        Обновленный список товаров
    """
    return get_catalog(force_reload=True)


def search_catalog(query: str, max_results: int = 5) -> list[dict]:
    """
    Поиск товаров в каталоге по запросу.

    Args:
        query: Поисковый запрос
        max_results: Максимальное количество результатов

    Returns:
        Список найденных товаров
    """
    from gdrive.photo_mapper import tokenize_text

    catalog = get_catalog()
    if not catalog:
        return []

    query_tokens = tokenize_text(query.lower())
    if not query_tokens:
        return []

    # Оценка релевантности для каждого товара
    scored_products = []
    for product in catalog:
        # Формируем строку для поиска из всех полей товара
        search_text = " ".join([
            product.get("name", ""),
            product.get("category", ""),
            product.get("colors", ""),
            product.get("descriptions", ""),
        ]).lower()

        product_tokens = tokenize_text(search_text)
        if not product_tokens:
            continue

        # Считаем пересечение токенов
        overlap = query_tokens & product_tokens
        if not overlap:
            continue

        # Простая оценка: количество совпавших токенов
        score = len(overlap)
        scored_products.append((score, product))

    # Сортируем по релевантности
    scored_products.sort(key=lambda x: x[0], reverse=True)

    # Возвращаем топ N результатов
    return [product for score, product in scored_products[:max_results]]


def format_product_for_prompt(product: dict) -> str:
    """
    Форматирует товар для включения в промпт.

    Args:
        product: Словарь с данными о товаре

    Returns:
        Отформатированная строка
    """
    parts = []

    name = product.get("name", "").strip()
    if name:
        parts.append(f"Товар: {name}")

    category = product.get("category", "").strip()
    if category:
        parts.append(f"Категория: {category}")

    price = product.get("price", "").strip()
    if price:
        parts.append(f"Цена: {price}")

    colors = product.get("colors", "").strip()
    if colors:
        parts.append(f"Цвета: {colors}")

    desc = product.get("descriptions", "").strip()
    if desc:
        parts.append(f"Описание: {desc}")

    return "\n".join(parts)
