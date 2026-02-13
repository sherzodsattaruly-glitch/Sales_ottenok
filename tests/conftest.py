"""
Конфигурация и фикстуры для тестов.
"""

import pytest
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path


@pytest.fixture
def test_inventory_df():
    """Тестовый DataFrame с товарами."""
    data = [
        {
            "product_name": "Chanel Jumbo Classic Flap",
            "size": "-",
            "color": "черные",
            "quantity": 2,
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
    ]
    return pd.DataFrame(data)


@pytest.fixture
def mock_inventory_loader(monkeypatch, test_inventory_df):
    """Мок для InventoryLoader."""
    def mock_get_inventory_df(*args, **kwargs):
        return test_inventory_df

    monkeypatch.setattr(
        "inventory.stock_checker.get_inventory_df",
        mock_get_inventory_df
    )


@pytest.fixture
def test_client_data():
    """Тестовые данные клиента для дожима."""
    return {
        "chat_id": "77001234567@c.us",
        "last_client_message_at": datetime.now() - timedelta(hours=4),
        "last_bot_message_at": datetime.now() - timedelta(hours=3, minutes=55),
        "nudge_count": 0,
        "last_client_text": "Хорошо, спасибо",
        "handoff_enabled": False,
    }
