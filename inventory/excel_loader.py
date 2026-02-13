"""
Загрузка Excel файла с наличием товаров.
Кэширует DataFrame в памяти на INVENTORY_CACHE_TTL секунд.
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from config import INVENTORY_EXCEL_PATH, INVENTORY_CACHE_TTL

logger = logging.getLogger(__name__)


class InventoryLoader:
    """Загрузчик Excel файла с кэшированием."""

    def __init__(self):
        self._cache: Optional[pd.DataFrame] = None
        self._cache_time: Optional[datetime] = None
        self._cache_ttl = timedelta(seconds=INVENTORY_CACHE_TTL)

    def load_inventory(self, force_reload: bool = False) -> pd.DataFrame:
        """
        Загрузить Excel файл с наличием товаров.

        Args:
            force_reload: Принудительная перезагрузка (игнорировать кэш)

        Returns:
            DataFrame с колонками: product_name, size, color, quantity, price
        """
        now = datetime.now()

        # Проверяем кэш
        if not force_reload and self._cache is not None and self._cache_time is not None:
            if now - self._cache_time < self._cache_ttl:
                logger.debug("Используем кэшированный inventory (age: %s)", now - self._cache_time)
                return self._cache

        # Загружаем из файла
        try:
            path = Path(INVENTORY_EXCEL_PATH)
            if not path.exists():
                logger.warning("Inventory файл не найден: %s", path)
                return pd.DataFrame(columns=["product_name", "size", "color", "quantity", "price"])

            logger.info("Загружаем inventory из %s", path)
            df = pd.read_excel(path, engine="openpyxl")

            # Нормализуем названия колонок
            df.columns = [col.strip().lower() for col in df.columns]

            # Проверяем необходимые колонки
            required_cols = ["product_name", "size", "color", "quantity", "price"]
            missing_cols = [col for col in required_cols if col not in df.columns]
            if missing_cols:
                logger.error("В Excel файле отсутствуют колонки: %s", missing_cols)
                return pd.DataFrame(columns=required_cols)

            # Заменяем NaN на пустые строки для текстовых полей
            for col in ["product_name", "size", "color", "price"]:
                df[col] = df[col].fillna("").astype(str).str.strip()

            # Заменяем NaN на 0 для quantity
            df["quantity"] = df["quantity"].fillna(0).astype(int)

            # Фильтруем пустые названия товаров
            df = df[df["product_name"] != ""]

            # Обновляем кэш
            self._cache = df
            self._cache_time = now

            logger.info("Загружено %d товаров из Excel", len(df))
            return df

        except Exception as e:
            logger.error("Ошибка загрузки inventory из %s: %s", INVENTORY_EXCEL_PATH, e, exc_info=True)
            # В случае ошибки возвращаем пустой DataFrame
            return pd.DataFrame(columns=["product_name", "size", "color", "quantity", "price"])


# Singleton instance
_inventory_loader = InventoryLoader()


def get_inventory_df(force_reload: bool = False) -> pd.DataFrame:
    """
    Получить DataFrame с наличием товаров (с кэшированием).

    Args:
        force_reload: Принудительная перезагрузка из Excel

    Returns:
        DataFrame с товарами
    """
    return _inventory_loader.load_inventory(force_reload=force_reload)


def reload_inventory() -> pd.DataFrame:
    """
    Принудительно перезагрузить inventory из Excel.

    Returns:
        Обновленный DataFrame
    """
    return get_inventory_df(force_reload=True)
