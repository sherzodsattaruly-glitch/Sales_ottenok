"""
Загрузка каталога товаров из Google Sheets.
Кэширует DataFrame в памяти на CATALOG_CACHE_TTL секунд.

Google Sheet формат:
  name | category | price | colors | descriptions | кол-во сумки | 35 | 36 | ... | 42

Возвращает DataFrame в формате (product_name, size, color, quantity, price)
— по одной строке на каждый размер (unpivot колонок 35-42).
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from config import CATALOG_SHEETS_ID, CATALOG_CACHE_TTL, GOOGLE_CREDENTIALS_FILE

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
SIZE_COLUMNS = ["35", "36", "37", "38", "39", "40", "41", "42"]
REQUIRED_OUTPUT_COLS = ["product_name", "size", "color", "quantity", "price"]

_sheets_service = None


def _get_sheets_service():
    global _sheets_service
    if _sheets_service is None:
        creds = Credentials.from_service_account_file(
            GOOGLE_CREDENTIALS_FILE, scopes=SCOPES
        )
        _sheets_service = build("sheets", "v4", credentials=creds)
    return _sheets_service


def _fetch_sheet_data() -> pd.DataFrame:
    """Загрузить данные из Google Sheets и вернуть сырой DataFrame."""
    service = _get_sheets_service()
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=CATALOG_SHEETS_ID, range="A1:N1000")
        .execute()
    )
    rows = result.get("values", [])
    if not rows:
        return pd.DataFrame()

    headers = [str(h).strip().lower() for h in rows[0]]
    data = []
    for row in rows[1:]:
        # Дополняем короткие строки пустыми значениями
        padded = row + [""] * (len(headers) - len(row))
        data.append(dict(zip(headers, padded)))

    return pd.DataFrame(data)


def _unpivot_sizes(df: pd.DataFrame) -> pd.DataFrame:
    """
    Развернуть колонки размеров (35-42) в строки.

    Из одной строки с колонками 35=2, 36=0, 37=1 получаем 3 строки:
      product_name | size | color | quantity | price
      ...          | 35   | ...   | 2        | ...
      ...          | 36   | ...   | 0        | ...
      ...          | 37   | ...   | 1        | ...

    Для товаров без размеров (сумки) — берём количество из колонки "кол-во сумки".
    """
    result_rows = []

    for _, row in df.iterrows():
        product_name = str(row.get("name", "")).strip()
        if not product_name:
            continue

        color = str(row.get("colors", "")).strip()
        price = str(row.get("price", "")).strip()
        category = str(row.get("category", "")).strip().lower()

        # Проверяем, есть ли хоть один размер заполнен
        has_sizes = False
        for sc in SIZE_COLUMNS:
            val = str(row.get(sc, "")).strip()
            if val and val != "0":
                has_sizes = True
                break

        if has_sizes:
            # Разворачиваем каждый размер в отдельную строку
            for sc in SIZE_COLUMNS:
                val = str(row.get(sc, "")).strip()
                qty = int(val) if val.isdigit() else 0
                result_rows.append({
                    "product_name": product_name,
                    "size": sc,
                    "color": color,
                    "quantity": qty,
                    "price": price,
                })
        else:
            # Нет размеров — берём количество из колонки "кол-во сумки"
            bag_qty_val = str(row.get("кол-во сумки", "")).strip()
            bag_qty = int(bag_qty_val) if bag_qty_val.isdigit() else 0
            result_rows.append({
                "product_name": product_name,
                "size": "",
                "color": color,
                "quantity": bag_qty,
                "price": price,
            })

    return pd.DataFrame(result_rows, columns=REQUIRED_OUTPUT_COLS)


class InventoryLoader:
    """Загрузчик каталога из Google Sheets с кэшированием."""

    def __init__(self):
        self._cache: Optional[pd.DataFrame] = None
        self._cache_time: Optional[datetime] = None
        self._cache_ttl = timedelta(seconds=CATALOG_CACHE_TTL)

    def load_inventory(self, force_reload: bool = False) -> pd.DataFrame:
        """
        Загрузить каталог из Google Sheets.

        Returns:
            DataFrame с колонками: product_name, size, color, quantity, price
        """
        now = datetime.now()

        if not force_reload and self._cache is not None and self._cache_time is not None:
            if now - self._cache_time < self._cache_ttl:
                logger.debug("Используем кэшированный inventory (age: %s)", now - self._cache_time)
                return self._cache

        try:
            if not CATALOG_SHEETS_ID:
                logger.warning("CATALOG_SHEETS_ID не задан")
                return pd.DataFrame(columns=REQUIRED_OUTPUT_COLS)

            logger.info("Загружаем каталог из Google Sheets: %s", CATALOG_SHEETS_ID)
            raw_df = _fetch_sheet_data()

            if raw_df.empty:
                logger.warning("Google Sheet пуст")
                return pd.DataFrame(columns=REQUIRED_OUTPUT_COLS)

            df = _unpivot_sizes(raw_df)

            self._cache = df
            self._cache_time = now

            logger.info("Загружено %d строк из Google Sheets (%d уникальных товаров)",
                        len(df), df["product_name"].nunique())
            return df

        except Exception as e:
            logger.error("Ошибка загрузки каталога из Google Sheets: %s", e, exc_info=True)
            # Возвращаем кэш если есть, иначе пустой DataFrame
            if self._cache is not None:
                logger.info("Используем устаревший кэш после ошибки")
                return self._cache
            return pd.DataFrame(columns=REQUIRED_OUTPUT_COLS)


# Singleton
_inventory_loader = InventoryLoader()


def get_inventory_df(force_reload: bool = False) -> pd.DataFrame:
    """Получить DataFrame с товарами (с кэшированием)."""
    return _inventory_loader.load_inventory(force_reload=force_reload)


def reload_inventory() -> pd.DataFrame:
    """Принудительно перезагрузить каталог из Google Sheets."""
    return get_inventory_df(force_reload=True)
