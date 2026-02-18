"""
Live-тесты проверки наличия товаров по реальным данным из Google Sheets.

Запуск:
    pytest tests/test_inventory_live.py -m live -v

Требует настроенных credentials (GOOGLE_CREDENTIALS_FILE, CATALOG_SHEETS_ID).
Пропускаются автоматически если инвентарь не загружается.
"""

import pytest
from inventory.stock_checker import check_product_availability
from inventory.excel_loader import get_inventory_df


def _inventory_available():
    """Проверить, доступен ли каталог из Google Sheets."""
    try:
        df = get_inventory_df()
        return not df.empty
    except Exception:
        return False


pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def inventory_df():
    df = get_inventory_df()
    if df.empty:
        pytest.skip("Инвентарь не загружен из Google Sheets (проверь CATALOG_SHEETS_ID и credentials)")
    return df


def test_inventory_loads(inventory_df):
    """Каталог успешно загружается из Google Sheets."""
    assert not inventory_df.empty
    assert "product_name" in inventory_df.columns
    assert "quantity" in inventory_df.columns
    print(f"\nЗагружено товаров: {len(inventory_df)} строк, {inventory_df['product_name'].nunique()} уникальных")


def test_all_catalog_products_findable(inventory_df):
    """Каждый товар из каталога находит сам себя через check_product_availability."""
    failures = []
    for product_name in inventory_df["product_name"].unique():
        result = check_product_availability(product_name)
        if len(result["matches"]) == 0:
            failures.append(product_name)

    if failures:
        pytest.fail(
            f"Следующие товары не находят сами себя ({len(failures)} шт.):\n"
            + "\n".join(f"  - {p}" for p in failures[:10])
            + ("\n  ..." if len(failures) > 10 else "")
        )


def test_availability_result_structure(inventory_df):
    """check_product_availability возвращает правильную структуру данных."""
    product_name = inventory_df["product_name"].iloc[0]
    result = check_product_availability(product_name)

    assert "available" in result, "Нет поля 'available'"
    assert "matches" in result, "Нет поля 'matches'"
    assert "quantity" in result, "Нет поля 'quantity'"
    assert "price" in result, "Нет поля 'price'"
    assert isinstance(result["available"], bool)
    assert isinstance(result["matches"], list)
    assert isinstance(result["quantity"], int)


def test_size_filter_shoes(inventory_df):
    """Фильтрация по размеру работает корректно для обуви."""
    shoes = inventory_df[inventory_df["size"].str.match(r"^\d{2}$", na=False)]
    if shoes.empty:
        pytest.skip("Нет обуви с числовыми размерами в каталоге")

    row = shoes.iloc[0]
    product_name = row["product_name"]
    correct_size = row["size"]

    result_ok = check_product_availability(product_name, size=correct_size)
    result_bad = check_product_availability(product_name, size="99")

    assert len(result_ok["matches"]) >= 1, f"Товар '{product_name}' размер {correct_size} не найден"
    assert result_bad["available"] is False, "Размер 99 не должен быть доступен"
    assert len(result_bad["matches"]) == 0, "Размер 99 не должен давать совпадений"


def test_color_filter(inventory_df):
    """Фильтрация по цвету работает корректно."""
    colored = inventory_df[inventory_df["color"].str.len() > 0]
    if colored.empty:
        pytest.skip("Нет товаров с указанным цветом в каталоге")

    row = colored.iloc[0]
    product_name = row["product_name"]
    correct_color = row["color"]

    result_ok = check_product_availability(product_name, color=correct_color)
    result_bad = check_product_availability(product_name, color="несуществующийцвет123")

    assert len(result_ok["matches"]) >= 1, f"'{product_name}' цвет '{correct_color}' не найден"
    assert len(result_bad["matches"]) == 0, "Несуществующий цвет не должен давать совпадений"


def test_chanel_ballet_pink_36():
    """Chanel балетки розовые размер 36 — конкретный кейс из бизнес-задачи."""
    if not _inventory_available():
        pytest.skip("Каталог недоступен")

    result = check_product_availability("Chanel балетки", size="36", color="розовые")

    assert "available" in result
    assert isinstance(result["available"], bool)

    if result["available"]:
        print(f"\nChanel балетки розовые 36: ЕСТЬ в наличии (qty={result['quantity']})")
    else:
        print(f"\nChanel балетки розовые 36: НЕТ в наличии — будет предзаказ ✓")


def test_nonexistent_product():
    """Несуществующий товар возвращает available=False с пустым списком совпадений."""
    if not _inventory_available():
        pytest.skip("Каталог недоступен")

    result = check_product_availability("ТоварКоторогоТочноНетВКаталоге12345")
    assert result["available"] is False
    assert len(result["matches"]) == 0
    assert result["quantity"] == 0
