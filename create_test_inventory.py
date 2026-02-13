"""
Скрипт для создания тестового Excel файла с наличием товаров.
Запуск: python create_test_inventory.py
"""

import pandas as pd
from pathlib import Path

# Тестовые данные
data = [
    {
        "product_name": "Chanel Jumbo Classic Flap",
        "size": "-",
        "color": "черные",
        "quantity": 2,
        "price": "45000₸"
    },
    {
        "product_name": "Chanel Jumbo Classic Flap",
        "size": "-",
        "color": "бежевые",
        "quantity": 1,
        "price": "45000₸"
    },
    {
        "product_name": "Jimmy Choo Azia 95",
        "size": "38",
        "color": "розовые",
        "quantity": 1,
        "price": "38000₸"
    },
    {
        "product_name": "Jimmy Choo Azia 95",
        "size": "39",
        "color": "розовые",
        "quantity": 0,
        "price": "38000₸"
    },
    {
        "product_name": "Valentino Garavani",
        "size": "38",
        "color": "белые",
        "quantity": 3,
        "price": "42000₸"
    },
    {
        "product_name": "Valentino Garavani",
        "size": "39",
        "color": "белые",
        "quantity": 2,
        "price": "42000₸"
    },
    {
        "product_name": "Balenciaga Triple S",
        "size": "37",
        "color": "черные",
        "quantity": 0,
        "price": "55000₸"
    },
]

def create_inventory_excel():
    """Создать тестовый Excel файл."""
    df = pd.DataFrame(data)

    # Создаем папку data если её нет
    Path("data").mkdir(exist_ok=True)

    # Сохраняем в Excel
    output_path = "data/inventory.xlsx"
    df.to_excel(output_path, index=False, engine="openpyxl")
    print(f"[OK] Создан файл: {output_path}")
    print(f"Товаров в наличии: {len(df)}")
    print(f"Уникальных моделей: {df['product_name'].nunique()}")

if __name__ == "__main__":
    create_inventory_excel()
