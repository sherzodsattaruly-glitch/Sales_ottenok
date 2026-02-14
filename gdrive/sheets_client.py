"""
Google Sheets API client для загрузки каталога товаров.
"""

import logging
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import GOOGLE_CREDENTIALS_FILE

logger = logging.getLogger(__name__)


def get_sheets_service():
    """Создает сервис Google Sheets API с service account credentials."""
    from google.oauth2 import service_account

    credentials = service_account.Credentials.from_service_account_file(
        GOOGLE_CREDENTIALS_FILE,
        scopes=['https://www.googleapis.com/auth/spreadsheets.readonly']
    )

    return build('sheets', 'v4', credentials=credentials)


def read_catalog_from_sheets(spreadsheet_id: str, range_name: str = "A1:Z1000") -> list[dict[str, Any]]:
    """
    Читает каталог товаров из Google Sheets.

    Args:
        spreadsheet_id: ID Google Sheets документа
        range_name: Диапазон ячеек для чтения (по умолчанию A1:Z1000)

    Returns:
        Список словарей с данными о товарах. Первая строка = заголовки колонок.
    """
    try:
        service = get_sheets_service()

        # Читаем данные из таблицы
        result = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=range_name
        ).execute()

        values = result.get('values', [])

        if not values:
            logger.warning(f"No data found in spreadsheet {spreadsheet_id}")
            return []

        # Первая строка - заголовки
        headers = values[0]
        logger.info(f"Found headers: {headers}")

        # Преобразуем строки в словари
        products = []
        for row in values[1:]:  # Пропускаем первую строку с заголовками
            if not row:  # Пропускаем пустые строки
                continue

            # Дополняем строку пустыми значениями если колонок меньше чем заголовков
            while len(row) < len(headers):
                row.append("")

            product = {}
            for i, header in enumerate(headers):
                product[header] = row[i] if i < len(row) else ""

            products.append(product)

        logger.info(f"Loaded {len(products)} products from Google Sheets")
        return products

    except HttpError as error:
        logger.error(f"Failed to read Google Sheets: {error}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error reading Google Sheets: {e}")
        raise


def get_catalog_metadata(spreadsheet_id: str) -> dict[str, Any]:
    """
    Получает метаданные о таблице (название, листы и т.д.)

    Args:
        spreadsheet_id: ID Google Sheets документа

    Returns:
        Словарь с метаданными
    """
    try:
        service = get_sheets_service()

        spreadsheet = service.spreadsheets().get(
            spreadsheetId=spreadsheet_id
        ).execute()

        return {
            'title': spreadsheet.get('properties', {}).get('title', ''),
            'sheets': [
                {
                    'title': sheet['properties']['title'],
                    'sheetId': sheet['properties']['sheetId'],
                    'index': sheet['properties']['index']
                }
                for sheet in spreadsheet.get('sheets', [])
            ]
        }

    except HttpError as error:
        logger.error(f"Failed to get spreadsheet metadata: {error}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error getting metadata: {e}")
        raise


if __name__ == "__main__":
    # Тест для проверки работы
    logging.basicConfig(level=logging.INFO)

    SPREADSHEET_ID = "13jPysMCvaSGJPV3ejWJVa8rpZDD2HxPEPRxSIUbst78"

    # Получаем метаданные
    print("=== Metadata ===")
    metadata = get_catalog_metadata(SPREADSHEET_ID)
    print(f"Title: {metadata['title']}")
    print(f"Sheets: {metadata['sheets']}")
    print()

    # Читаем данные
    print("=== Catalog Data ===")
    products = read_catalog_from_sheets(SPREADSHEET_ID)
    print(f"Total products: {len(products)}")

    if products:
        print(f"\nFirst product:")
        for key, value in products[0].items():
            print(f"  {key}: {value}")
