"""
Тесты для модуля проверки наличия товаров (inventory.stock_checker).
"""

import pytest
from inventory.stock_checker import check_product_availability, format_availability_message


def test_check_availability_exact_match(mock_inventory_loader):
    """Тест проверки наличия товара по точному совпадению."""
    result = check_product_availability("Chanel Jumbo Classic Flap")

    assert result["available"] is True
    assert result["quantity"] == 2
    assert result["price"] == "45000₸"
    assert len(result["matches"]) >= 1


def test_check_availability_with_size_filter(mock_inventory_loader):
    """Тест проверки наличия с фильтрацией по размеру."""
    result = check_product_availability("Jimmy Choo Azia", size="38")

    assert result["available"] is True
    assert result["quantity"] == 1
    assert len(result["matches"]) == 1
    assert result["matches"][0]["size"] == "38"


def test_check_availability_out_of_stock(mock_inventory_loader):
    """Тест проверки товара который закончился."""
    result = check_product_availability("Jimmy Choo Azia", size="39")

    assert result["available"] is False  # quantity=0
    assert result["quantity"] == 0
    assert len(result["matches"]) == 1


def test_check_availability_not_found(mock_inventory_loader):
    """Тест проверки несуществующего товара."""
    result = check_product_availability("Nonexistent Product")

    assert result["available"] is False
    assert result["quantity"] == 0
    assert len(result["matches"]) == 0


def test_check_availability_with_color_filter(mock_inventory_loader):
    """Тест проверки наличия с фильтрацией по цвету."""
    result = check_product_availability("Valentino", color="белые")

    assert result["available"] is True
    assert result["quantity"] == 3
    assert len(result["matches"]) >= 1


def test_format_availability_message_in_stock(mock_inventory_loader):
    """Тест форматирования сообщения когда товар есть."""
    availability = check_product_availability("Chanel Jumbo")
    message = format_availability_message(availability, "Chanel Jumbo")

    assert "есть в наличии" in message
    assert "45000₸" in message


def test_format_availability_message_out_of_stock(mock_inventory_loader):
    """Тест форматирования сообщения когда товар закончился."""
    availability = check_product_availability("Jimmy Choo Azia", size="39")
    message = format_availability_message(availability, "Jimmy Choo Azia")

    assert "нет в наличии" in message


def test_format_availability_message_not_found(mock_inventory_loader):
    """Тест форматирования сообщения когда товар не найден."""
    availability = check_product_availability("Unknown Product")
    message = format_availability_message(availability, "Unknown Product")

    assert "не вижу в наличии" in message
    assert "похожий вариант" in message
