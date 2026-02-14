"""
Smoke tests: типичные сценарии клиента от начала до конца.

Проверяют ВЕСЬ пайплайн generate_response() с замоканным OpenAI,
Google Drive и GREEN-API. База данных — временная SQLite.

Запуск:
    pytest tests/test_smoke.py -v
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from ai.order_manager import _ORDER_CONFIRM_TEXT


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_completion(content: str):
    m = MagicMock()
    m.choices = [MagicMock()]
    m.choices[0].message.content = content
    return m


def _fields_json(**overrides) -> str:
    fields = {
        "city": "",
        "product": "",
        "product_type": "",
        "size": "",
        "color": "",
        "address": "",
        "ready_to_order": False,
    }
    fields.update(overrides)
    return json.dumps(fields, ensure_ascii=False)


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    import ai.engine as engine_mod
    path = str(tmp_path / "test.db")
    monkeypatch.setattr("db.models.SQLITE_DB_PATH", path)
    monkeypatch.setattr("db.conversations.SQLITE_DB_PATH", path)
    engine_mod._COLOR_REQUIREMENT_CACHE.clear()
    from db.models import init_db
    init_db()
    return path


@pytest.fixture
def mock_openai(monkeypatch):
    mock_create = AsyncMock()
    monkeypatch.setattr("ai.engine.openai_client.chat.completions.create", mock_create)
    return mock_create


@pytest.fixture
def mock_rag(monkeypatch):
    monkeypatch.setattr("ai.engine.search_products", AsyncMock(return_value=[]))
    monkeypatch.setattr("ai.engine.search_scripts", AsyncMock(return_value=[]))


@pytest.fixture
def mock_photos(monkeypatch):
    mock_find = AsyncMock(return_value=[])
    monkeypatch.setattr("ai.engine.find_product_photos", mock_find)
    return mock_find


@pytest.fixture
def mock_inventory(monkeypatch):
    def mock_check(*args, **kwargs):
        return {"available": True, "quantity": 1, "price": "38000₸", "matches": []}

    def mock_format(availability, product_name):
        return f"{product_name} есть в наличии (38000₸)"

    monkeypatch.setattr("ai.engine.check_product_availability", mock_check)
    monkeypatch.setattr("ai.engine.format_availability_message", mock_format)


# ── Smoke Scenarios ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_smoke_greeting(db_path, mock_openai, mock_rag, mock_photos):
    """Сценарий 1: Клиент здоровается → приветствие, нет вопросов про заказ."""
    from ai.engine import generate_response

    mock_openai.side_effect = [
        _make_completion(_fields_json()),
        _make_completion("Здравствуйте! ✨|||Рады приветствовать вас в бутике Ottenok!"),
    ]

    result = await generate_response("smoke_1@c.us", "Здравствуйте", "Клиент")

    assert "здравствуйте" in result["text"].lower()
    assert _ORDER_CONFIRM_TEXT not in result["text"]
    assert result["photos"] == []


@pytest.mark.asyncio
async def test_smoke_category_browsing_bags(db_path, mock_openai, mock_rag, mock_photos):
    """Сценарий 2: 'Какие сумки у вас есть' → покажу + фото + НЕТ лишних вопросов."""
    from ai.engine import generate_response

    mock_photos.return_value = [
        {"file_id": "miumiu1", "filename": "сумка Miu Miu Arcadie 1.jpg", "direct_url": ""},
        {"file_id": "ysl1", "filename": "Сумка черная Yves Saint Laurent Monogram.jpg", "direct_url": ""},
        {"file_id": "chanel25_1", "filename": "Сумка черная Chanel 25 1.jpg", "direct_url": ""},
    ]

    mock_openai.side_effect = [
        _make_completion(_fields_json(product_type="bag")),
        _make_completion("Сейчас покажу какие есть 😊"),
    ]

    result = await generate_response("smoke_2@c.us", "Какие сумки у вас есть", "Клиент")

    text_lower = result["text"].lower()
    # Должен сказать "покажу"
    assert "покажу" in text_lower, f"Expected 'покажу' in response: {result['text']}"
    # НЕ должен спрашивать адрес/город
    assert "адрес" not in text_lower, f"Should NOT ask for address: {result['text']}"
    # Должны быть фото
    assert len(result["photos"]) >= 1, f"Expected photos, got none"
    # Product НЕ должен быть назначен
    assert result["order_context"].get("product") == "" or not result["order_context"].get("product")


@pytest.mark.asyncio
async def test_smoke_specific_product_then_city(db_path, mock_openai, mock_rag, mock_photos):
    """Сценарий 3+4: 'Хочу Chanel 25' → описание. Потом 'Алматы' → продолжение."""
    from ai.engine import generate_response

    # Шаг 1: клиент выбрал товар
    mock_openai.side_effect = [
        _make_completion(_fields_json(product="Chanel 25", product_type="bag")),
        _make_completion("Отличный выбор! Сумка Chanel 25 — это классика|||А с какого вы города?"),
    ]

    result1 = await generate_response("smoke_3@c.us", "хочу Chanel 25", "Клиент")
    assert result1["order_context"]["product"] == "Chanel 25"

    # Шаг 2: клиент назвал город
    mock_openai.side_effect = [
        _make_completion(_fields_json(city="Алматы")),
        _make_completion("Отлично!"),
    ]

    result2 = await generate_response("smoke_3@c.us", "Алматы", "Клиент")
    assert result2["order_context"]["city"] == "Алматы"
    # НЕ должен сразу спрашивать адрес (сначала размер/цвет если нужно)
    assert _ORDER_CONFIRM_TEXT not in result2["text"]


@pytest.mark.asyncio
async def test_smoke_price_question(db_path, mock_openai, mock_rag, mock_photos):
    """Сценарий 5: 'Сколько стоит?' → цена + обоснование, не заказ."""
    from ai.engine import generate_response

    mock_openai.side_effect = [
        _make_completion(_fields_json()),
        _make_completion("Цена 45 000₸|||Это выгоднее большинства байеров при таком же уровне качества"),
    ]

    result = await generate_response("smoke_5@c.us", "сколько стоит?", "Клиент")
    assert _ORDER_CONFIRM_TEXT not in result["text"]


@pytest.mark.asyncio
async def test_smoke_objection_dorogo(db_path, mock_openai, mock_rag, mock_photos):
    """Сценарий 6: 'Дорого' → отработка возражения, не заказ."""
    from ai.engine import generate_response

    mock_openai.side_effect = [
        _make_completion(_fields_json()),
        _make_completion("Понимаю🤍|||Важно учитывать, что вы платите за качество и контроль"),
    ]

    result = await generate_response("smoke_6@c.us", "дорого", "Клиент")
    assert _ORDER_CONFIRM_TEXT not in result["text"]


@pytest.mark.asyncio
async def test_smoke_full_order_flow(db_path, mock_openai, mock_rag, mock_photos, mock_inventory):
    """Полный flow: товар → город → размер → адрес → оформление."""
    from ai.engine import generate_response

    chat_id = "smoke_full@c.us"

    # 1. Выбор товара
    mock_openai.side_effect = [
        _make_completion(_fields_json(product="Jimmy Choo Azia 95", product_type="shoes")),
        _make_completion("Отличный выбор!|||Подскажите, из какого вы города?"),
    ]
    r1 = await generate_response(chat_id, "хочу Jimmy Choo Azia", "Клиент")
    assert r1["order_context"]["product"] == "Jimmy Choo Azia 95"

    # 2. Город
    mock_openai.side_effect = [
        _make_completion(_fields_json(city="Астана")),
        _make_completion("Отлично!"),
    ]
    r2 = await generate_response(chat_id, "Астана", "Клиент")
    assert r2["order_context"]["city"] == "Астана"

    # 3. Размер
    mock_openai.side_effect = [
        _make_completion(_fields_json(size="38")),
        _make_completion("Записала!"),
    ]
    r3 = await generate_response(chat_id, "38 размер", "Клиент")
    assert r3["order_context"]["size"] == "38"

    # 4. Адрес + оформление
    mock_openai.side_effect = [
        _make_completion(_fields_json(address="ул. Абая 1", ready_to_order=True)),
        _make_completion("Сейчас проверю наличие!"),
    ]
    r4 = await generate_response(chat_id, "ул. Абая 1, оформляю", "Клиент")
    assert r4["order_context"]["address"] == "ул. Абая 1"
    assert _ORDER_CONFIRM_TEXT in r4["text"]
