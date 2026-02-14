"""
Integration tests for ai.engine.generate_response and handle_message.

External dependencies (OpenAI, Google Drive, RAG) are fully mocked.
DB uses a temporary SQLite file initialized with init_db().
"""

import json
import pytest
import aiosqlite
from unittest.mock import AsyncMock, MagicMock

from ai.order_manager import _ORDER_CONFIRM_TEXT


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_completion(content: str):
    """Create a mock OpenAI ChatCompletion response object."""
    m = MagicMock()
    m.choices = [MagicMock()]
    m.choices[0].message.content = content
    return m


def _fields_json(**overrides) -> str:
    """Build JSON string for _extract_order_fields mock response."""
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
    """Create temp SQLite DB, patch SQLITE_DB_PATH everywhere, init schema."""
    import ai.engine as engine_mod

    path = str(tmp_path / "test.db")
    monkeypatch.setattr("db.models.SQLITE_DB_PATH", path)
    monkeypatch.setattr("db.conversations.SQLITE_DB_PATH", path)

    # Clear color requirement cache between tests
    engine_mod._COLOR_REQUIREMENT_CACHE.clear()

    from db.models import init_db
    init_db()

    return path


@pytest.fixture
def mock_openai(monkeypatch):
    """Mock OpenAI client."""
    mock_create = AsyncMock()
    monkeypatch.setattr("ai.engine.openai_client.chat.completions.create", mock_create)
    return mock_create


@pytest.fixture
def mock_rag(monkeypatch):
    """Mock RAG functions."""
    monkeypatch.setattr("ai.engine.search_products", AsyncMock(return_value=[]))
    monkeypatch.setattr("ai.engine.search_scripts", AsyncMock(return_value=[]))


@pytest.fixture
def mock_photos(monkeypatch):
    """Mock photo search."""
    mock_find = AsyncMock(return_value=[])
    monkeypatch.setattr("ai.engine.find_product_photos", mock_find)
    return mock_find


@pytest.fixture
def mock_greenapi(monkeypatch):
    """Mock Green API send functions."""
    mock_text = AsyncMock()
    mock_images = AsyncMock()
    monkeypatch.setattr("ai.engine.send_text", mock_text)
    monkeypatch.setattr("ai.engine.send_multiple_images", mock_images)
    return {"send_text": mock_text, "send_images": mock_images}


@pytest.fixture
def mock_inventory(monkeypatch):
    """Mock inventory checker."""
    def mock_check(*args, **kwargs):
        return {
            "available": True,
            "quantity": 1,
            "price": "38000₸",
            "matches": [],
        }

    def mock_format(availability, product_name):
        return f"{product_name} есть в наличии (38000₸)"

    monkeypatch.setattr("ai.engine.check_product_availability", mock_check)
    monkeypatch.setattr("ai.engine.format_availability_message", mock_format)


# ── Test scenarios ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_new_client_greeting(db_path, mock_openai, mock_rag, mock_photos):
    """New client sends a greeting → response contains greeting, no checkout prompts."""
    from ai.engine import generate_response

    mock_openai.side_effect = [
        # 1st call: _extract_order_fields
        _make_completion(_fields_json()),
        # 2nd call: main GPT response
        _make_completion("Здравствуйте✨|||Рады приветствовать вас в нашем бутике Ottenok!"),
    ]

    result = await generate_response("test_chat@c.us", "Привет", "Тест")

    assert result["text"]
    assert result["is_new_client"] is True
    # No order confirm for a greeting
    assert _ORDER_CONFIRM_TEXT not in result["text"]
    # Should have greeting
    assert "здравствуйте" in result["text"].lower() or "привет" in result["text"].lower()


@pytest.mark.asyncio
async def test_address_question_no_order_flow(db_path, mock_openai, mock_rag, mock_photos):
    """Client asks 'какой у вас адрес?' → does NOT trigger order flow (TASK-1.2)."""
    from ai.engine import generate_response

    mock_openai.side_effect = [
        _make_completion(_fields_json()),
        _make_completion("Наш адрес: Егизбаева 7/2, Алматы|||Работаем ежедневно с 10:00 до 22:00"),
    ]

    result = await generate_response("test_chat@c.us", "какой у вас адрес?", "Тест")

    assert result["text"]
    # Should NOT trigger order confirmation
    assert _ORDER_CONFIRM_TEXT not in result["text"]
    # Should still have basic missing fields (no order started)
    assert "city" in result["missing_order_fields"]
    assert "product" in result["missing_order_fields"]


@pytest.mark.asyncio
async def test_city_answer_no_photos(db_path, mock_openai, mock_rag, mock_photos):
    """Client provides city (answering missing field) → no photos sent (TASK-1.4)."""
    from ai.engine import generate_response
    from db.conversations import save_message, upsert_order_context

    # Set up: bot already asked for city, client has a product in context
    await save_message("test_chat@c.us", "assistant", "Подскажите, из какого вы города?", "")
    await upsert_order_context("test_chat@c.us", {
        "city": "",
        "product": "Jimmy Choo Azia 95",
        "product_type": "shoes",
        "size": "",
        "color": "",
        "address": "",
    })

    mock_openai.side_effect = [
        _make_completion(_fields_json(city="Алматы")),
        _make_completion("Отлично, Алматы!|||Подскажите, какой размер вам нужен?"),
    ]

    result = await generate_response("test_chat@c.us", "Алматы", "Тест")

    assert result["text"]
    # Photos should NOT be sent when client is just answering a missing field
    assert result["photos"] == []
    # City should now be in context
    assert result["order_context"]["city"] == "Алматы"


@pytest.mark.asyncio
async def test_product_switch_resets_fields(db_path, mock_openai, mock_rag, mock_photos):
    """Client switches product → old size/color reset (TASK-1.6)."""
    from ai.engine import generate_response
    from db.conversations import upsert_order_context, get_order_context

    # Set up: client has Chanel bag with size M and color черный
    await upsert_order_context("test_chat@c.us", {
        "city": "Астана",
        "product": "Chanel Jumbo Classic Flap",
        "product_type": "bag",
        "size": "M",
        "color": "черный",
        "address": "",
    })

    mock_openai.side_effect = [
        _make_completion(_fields_json(product="Jimmy Choo Azia 95", product_type="shoes")),
        _make_completion("Отличный выбор! Jimmy Choo Azia 95|||Подскажите размер?"),
    ]

    result = await generate_response("test_chat@c.us", "хочу Jimmy Choo Azia 95", "Тест")

    # After product switch, size and color should be reset
    ctx = await get_order_context("test_chat@c.us")
    assert ctx["product"] == "Jimmy Choo Azia 95"
    assert ctx["product_type"] == "shoes"
    assert ctx["city"] == "Астана"  # City preserved
    assert ctx["size"] == ""  # Reset
    assert ctx["color"] == ""  # Reset


@pytest.mark.asyncio
async def test_order_completion_all_fields(db_path, mock_openai, mock_rag, mock_photos, mock_inventory):
    """Order completion → all fields present → _ORDER_CONFIRM_TEXT in response."""
    from ai.engine import generate_response
    from db.conversations import upsert_order_context

    # Set up: all required fields already collected
    await upsert_order_context("test_chat@c.us", {
        "city": "Алматы",
        "product": "Jimmy Choo Azia 95",
        "product_type": "shoes",
        "size": "38",
        "color": "",
        "address": "ул. Абая 1",
    })

    mock_openai.side_effect = [
        _make_completion(_fields_json(ready_to_order=True)),
        _make_completion("Сейчас всё проверю!"),
    ]

    result = await generate_response("test_chat@c.us", "оформляю заказ", "Тест")

    assert result["text"]
    # Should contain order confirmation text
    assert _ORDER_CONFIRM_TEXT in result["text"]
    # All fields should be present
    assert result["missing_order_fields"] == []


@pytest.mark.asyncio
async def test_handoff_enabled_skips_response(db_path, mock_openai, mock_rag, mock_photos, mock_greenapi):
    """Handoff enabled → message saved but no response generated (TASK-1.7)."""
    from ai.engine import handle_message
    from db.conversations import set_handoff_state

    chat_id = "test_client@c.us"

    # Enable handoff for this client
    await set_handoff_state(chat_id, True)

    # Call handle_message
    await handle_message(chat_id, "Тест", "хочу купить сумку")

    # Verify: send_text was NOT called (no response sent)
    assert mock_greenapi["send_text"].call_count == 0

    # Verify: message WAS saved to DB
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT role, content FROM conversations WHERE chat_id = ?",
            (chat_id,),
        )
        rows = await cursor.fetchall()
        assert len(rows) == 1
        assert rows[0]["role"] == "user"
        assert rows[0]["content"] == "хочу купить сумку"


@pytest.mark.asyncio
async def test_rag_product_context_used(db_path, mock_openai, mock_rag, mock_photos):
    """RAG returns product → product name is extracted and used in order context."""
    from ai.engine import generate_response
    import ai.engine

    # Mock RAG to return a product result
    mock_product_result = {
        "text": "👠 Jimmy Choo Azia 95 — элегантные туфли-слингбэки",
        "metadata": {
            "product_name": "Jimmy Choo Azia 95",
            "photo_folder_id": "fake_folder_id",
        },
    }

    # Patch search_products to return our mock result
    async def mock_search_products(query):
        return [mock_product_result]

    import ai.engine as engine_mod
    original_search = engine_mod.search_products
    engine_mod.search_products = mock_search_products

    try:
        mock_openai.side_effect = [
            _make_completion(_fields_json(product="Jimmy Choo Azia 95", product_type="shoes")),
            _make_completion("Отличный выбор! Jimmy Choo Azia 95 — это туфли на каблуке|||Подскажите, из какого вы города?"),
        ]

        result = await generate_response("test_chat@c.us", "туфли jimmy choo", "Тест")

        # Product name from RAG should be in order context
        assert result["order_context"]["product"] == "Jimmy Choo Azia 95"
        assert result["order_context"]["product_type"] == "shoes"

    finally:
        # Restore original function
        engine_mod.search_products = original_search


# ── Category browsing tests ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_category_browsing_resets_order_context(db_path, mock_openai, mock_rag, mock_photos):
    """Category browsing ('какие сумки есть') resets old product from order context."""
    from ai.engine import generate_response
    from db.conversations import upsert_order_context, get_order_context

    # Set up: client previously had кроссовки in order context
    await upsert_order_context("test_chat@c.us", {
        "city": "Алматы",
        "product": "Golden Goose Super-Star",
        "product_type": "shoes",
        "size": "38",
        "color": "",
        "address": "",
    })

    mock_openai.side_effect = [
        _make_completion(_fields_json(product_type="bag")),
        _make_completion("Сейчас покажу какие есть 😊"),
    ]

    result = await generate_response("test_chat@c.us", "какие сумки у вас есть", "Тест")

    # Product should be cleared (client is browsing, not ordering)
    ctx = await get_order_context("test_chat@c.us")
    assert ctx.get("product") == "", f"Product should be cleared during category browsing, got: {ctx.get('product')}"


@pytest.mark.asyncio
async def test_category_browsing_no_forced_questions(db_path, mock_openai, mock_rag, mock_photos):
    """Category browsing should NOT append missing-field questions (address, city, etc.)."""
    from ai.engine import generate_response
    from db.conversations import upsert_order_context, save_message

    # Set up: existing conversation with product context
    await save_message("test_chat@c.us", "assistant", "Здравствуйте! Рады вас видеть!", "")
    await upsert_order_context("test_chat@c.us", {
        "city": "Алматы",
        "product": "Golden Goose Super-Star",
        "product_type": "shoes",
        "size": "38",
        "color": "",
        "address": "",
    })

    mock_openai.side_effect = [
        _make_completion(_fields_json(product_type="bag")),
        _make_completion("Сейчас покажу какие есть"),
    ]

    result = await generate_response("test_chat@c.us", "какие сумки у вас есть", "Тест")

    text = result["text"].lower()
    # Should NOT ask about address, city, size during category browsing
    assert "адрес" not in text, f"Should not ask for address during browsing: {result['text']}"
    assert "город" not in text or "какого" not in text, f"Should not ask for city during browsing: {result['text']}"


@pytest.mark.asyncio
async def test_category_browsing_sends_photos(db_path, mock_openai, mock_rag, mock_photos):
    """Category browsing should trigger photo search and return photos."""
    from ai.engine import generate_response

    # Mock photos to return bag photos
    bag_photos = [
        {"file_id": "miumiu1", "filename": "сумка Miu Miu Arcadie 1.jpg", "direct_url": ""},
        {"file_id": "ysl1", "filename": "Сумка черная Yves Saint Laurent Monogram.jpg", "direct_url": ""},
        {"file_id": "chanel25_1", "filename": "Сумка черная Chanel 25 1.jpg", "direct_url": ""},
    ]
    mock_photos.return_value = bag_photos

    mock_openai.side_effect = [
        _make_completion(_fields_json(product_type="bag")),
        _make_completion("Сейчас покажу какие есть"),
    ]

    result = await generate_response("test_chat@c.us", "какие сумки у вас есть", "Тест")

    # Should have photos
    assert len(result["photos"]) >= 1, f"Expected photos for category browsing, got: {result['photos']}"
