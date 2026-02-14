"""
Тесты для модуля бизнес-логики заказов (ai.order_manager).
Все тесты чистые — без моков, I/O и async.
"""

from ai.order_manager import (
    _normalize_product_type,
    _infer_product_type_from_text,
    _sanitize_order_context,
    _merge_order_context,
    _contains_order_confirm,
    _strip_order_confirm,
    _build_missing_fields,
    _question_for_missing,
    _has_question,
    _has_order_intent,
    _asks_for_field,
    _assistant_already_requests_missing,
    _strip_checkout_prompts,
    _get_product_color_overrides,
)


# ── _normalize_product_type ──────────────────────────────────────────────────


def test_normalize_product_type_shoes():
    assert _normalize_product_type("shoes") == "shoes"


def test_normalize_product_type_russian_shoes():
    assert _normalize_product_type("обувь") == "shoes"


def test_normalize_product_type_bag():
    assert _normalize_product_type("bag") == "bag"


def test_normalize_product_type_bags_plural():
    assert _normalize_product_type("bags") == "bag"


def test_normalize_product_type_russian_bag():
    assert _normalize_product_type("сумка") == "bag"


def test_normalize_product_type_clothes():
    assert _normalize_product_type("clothes") == "clothes"


def test_normalize_product_type_russian_clothes():
    assert _normalize_product_type("одежда") == "clothes"


def test_normalize_product_type_other():
    assert _normalize_product_type("other") == "other"


def test_normalize_product_type_unknown():
    assert _normalize_product_type("unknown") == ""


def test_normalize_product_type_empty():
    assert _normalize_product_type("") == ""


def test_normalize_product_type_none():
    assert _normalize_product_type(None) == ""


def test_normalize_product_type_whitespace():
    assert _normalize_product_type("  shoes  ") == "shoes"


# ── _infer_product_type_from_text ────────────────────────────────────────────


def test_infer_shoes_slingback():
    assert _infer_product_type_from_text("Chanel Classic Slingbacks") == "shoes"


def test_infer_shoes_jimmy_choo():
    assert _infer_product_type_from_text("Jimmy Choo Azia 95") == "shoes"


def test_infer_shoes_emoji():
    assert _infer_product_type_from_text("👠 модель на каблуке") == "shoes"


def test_infer_shoes_sneaker_emoji():
    assert _infer_product_type_from_text("👟 кроссовки") == "shoes"


def test_infer_bag_jumbo():
    assert _infer_product_type_from_text("Chanel Jumbo Classic Flap") == "bag"


def test_infer_bag_arcadie():
    assert _infer_product_type_from_text("Miu Miu Arcadie") == "bag"


def test_infer_bag_emoji():
    assert _infer_product_type_from_text("👜 сумка") == "bag"


def test_infer_clothes_dress():
    assert _infer_product_type_from_text("красивое платье") == "clothes"


def test_infer_empty_for_hello():
    assert _infer_product_type_from_text("hello") == ""


def test_infer_empty_for_none():
    assert _infer_product_type_from_text(None) == ""


def test_infer_shoes_russian_tufly():
    assert _infer_product_type_from_text("красные туфли") == "shoes"


def test_infer_shoes_opyum():
    assert _infer_product_type_from_text("Saint Laurent Opyum") == "shoes"


# ── _merge_order_context ─────────────────────────────────────────────────────


def test_merge_empty_base_applies_updates():
    base = {"city": "", "product": "", "product_type": "", "size": "", "color": "", "address": ""}
    updates = {"city": "Алматы", "product": "Chanel Jumbo Classic Flap", "product_type": "bag"}
    result = _merge_order_context(base, updates)
    assert result["city"] == "Алматы"
    assert result["product"] == "Chanel Jumbo Classic Flap"
    assert result["product_type"] == "bag"


def test_merge_only_nonempty_updates_overwrite():
    base = {"city": "Алматы", "product": "Chanel Jumbo", "product_type": "bag", "size": "M", "color": "", "address": ""}
    updates = {"city": "", "product": "", "product_type": "", "size": "", "color": "черный", "address": ""}
    result = _merge_order_context(base, updates)
    assert result["city"] == "Алматы"
    assert result["product"] == "Chanel Jumbo"
    assert result["size"] == "M"
    assert result["color"] == "черный"


def test_merge_product_switch_resets_dependent_fields():
    """TASK-1.6: при смене товара сбрасываются size, color, address."""
    base = {
        "city": "Астана", "product": "Chanel Jumbo Classic Flap",
        "product_type": "bag", "size": "M", "color": "черный", "address": "ул. Абая 1",
    }
    updates = {
        "city": "", "product": "Jimmy Choo Azia 95",
        "product_type": "shoes", "size": "", "color": "", "address": "",
    }
    result = _merge_order_context(base, updates)
    assert result["product"] == "Jimmy Choo Azia 95"
    assert result["city"] == "Астана"  # город НЕ сбрасывается
    assert result["size"] == ""  # сброшен
    assert result["color"] == ""  # сброшен
    assert result["address"] == ""  # сброшен


def test_merge_same_product_preserves_fields():
    """Уточнение того же товара не сбрасывает поля."""
    base = {
        "city": "Алматы", "product": "Chanel Jumbo Classic Flap",
        "product_type": "bag", "size": "", "color": "черный", "address": "",
    }
    updates = {
        "city": "", "product": "Chanel Jumbo Classic Flap",
        "product_type": "", "size": "", "color": "", "address": "ул. Абая 10",
    }
    result = _merge_order_context(base, updates)
    assert result["color"] == "черный"  # сохранён
    assert result["address"] == "ул. Абая 10"


def test_merge_city_not_reset_on_product_switch():
    base = {
        "city": "Караганда", "product": "Golden Goose Super-Star",
        "product_type": "shoes", "size": "38", "color": "", "address": "",
    }
    updates = {"city": "", "product": "Miu Miu Arcadie", "product_type": "bag", "size": "", "color": "", "address": ""}
    result = _merge_order_context(base, updates)
    assert result["city"] == "Караганда"


def test_merge_infers_product_type():
    """Если product_type не указан, выводится из product."""
    base = {"city": "", "product": "", "product_type": "", "size": "", "color": "", "address": ""}
    updates = {"city": "", "product": "Chanel Classic Slingbacks", "product_type": "", "size": "", "color": "", "address": ""}
    result = _merge_order_context(base, updates)
    assert result["product_type"] == "shoes"


# ── _build_missing_fields ────────────────────────────────────────────────────


def test_missing_all_empty():
    ctx = {"city": "", "product": "", "product_type": "", "size": "", "color": "", "address": ""}
    result = _build_missing_fields(ctx, color_required=False)
    assert result == ["city", "product"]


def test_missing_shoes_with_city_needs_size():
    ctx = {"city": "Алматы", "product": "Jimmy Choo Azia", "product_type": "shoes", "size": "", "color": "", "address": ""}
    result = _build_missing_fields(ctx, color_required=False)
    assert "size" in result
    assert "address" not in result  # адрес только после базовых полей


def test_missing_all_basic_present_needs_address():
    ctx = {"city": "Алматы", "product": "Jimmy Choo Azia", "product_type": "shoes", "size": "38", "color": "", "address": ""}
    result = _build_missing_fields(ctx, color_required=False)
    assert result == ["address"]


def test_missing_all_fields_present():
    ctx = {"city": "Алматы", "product": "Jimmy Choo Azia", "product_type": "shoes", "size": "38", "color": "", "address": "ул. Абая 1"}
    result = _build_missing_fields(ctx, color_required=False)
    assert result == []


def test_missing_bag_no_size_required():
    ctx = {"city": "Алматы", "product": "Chanel Jumbo", "product_type": "bag", "size": "", "color": "", "address": ""}
    result = _build_missing_fields(ctx, color_required=False)
    assert "size" not in result
    assert result == ["address"]


def test_missing_color_required_no_color():
    ctx = {"city": "Алматы", "product": "Jimmy Choo Azia", "product_type": "shoes", "size": "38", "color": "", "address": ""}
    result = _build_missing_fields(ctx, color_required=True)
    assert "color" in result
    assert "address" not in result  # адрес только после цвета


def test_missing_color_required_with_color_needs_address():
    ctx = {"city": "Алматы", "product": "Jimmy Choo Azia", "product_type": "shoes", "size": "38", "color": "бежевый", "address": ""}
    result = _build_missing_fields(ctx, color_required=True)
    assert result == ["address"]


# ── _has_order_intent ────────────────────────────────────────────────────────


def test_order_intent_oformit():
    assert _has_order_intent("хочу оформить заказ") is True


def test_order_intent_beru():
    assert _has_order_intent("беру эти туфли") is True


def test_order_intent_address_dostavki():
    """'адрес доставки' — это intent заказа."""
    assert _has_order_intent("адрес доставки: Алматы, ул. Абая 1") is True


def test_no_order_intent_question_address():
    """TASK-1.2: 'какой у вас адрес?' — НЕ intent заказа."""
    assert _has_order_intent("какой у вас адрес?") is False


def test_no_order_intent_price():
    assert _has_order_intent("сколько стоит?") is False


def test_no_order_intent_greeting():
    assert _has_order_intent("привет") is False


# ── _strip_checkout_prompts ──────────────────────────────────────────────────


def test_strip_short_checkout_message():
    text = "Зафиксировали ваш заказ!"
    result = _strip_checkout_prompts(text)
    assert result == ""


def test_strip_preserves_long_description():
    """TASK-1.8: длинное описание с checkout hint не удаляется."""
    long_text = "Отличный выбор! Chanel Jumbo Classic Flap — это культовая сумка, которая никогда не выходит из моды. " \
                "Материал: натуральная кожа. Фурнитура: золото. Оформить заказ можно прямо сейчас!"
    result = _strip_checkout_prompts(long_text)
    assert result == long_text  # >= 120 символов, сохранена


def test_strip_empty_string():
    assert _strip_checkout_prompts("") == ""


def test_strip_none():
    assert _strip_checkout_prompts(None) is None


def test_strip_multi_part_removes_only_checkout():
    text = "Вот фото этой модели|||Оформить заказ?"
    result = _strip_checkout_prompts(text)
    assert "Вот фото этой модели" in result
    assert "Оформить заказ" not in result


# ── _contains_order_confirm / _strip_order_confirm ───────────────────────────


def test_contains_order_confirm_exact():
    assert _contains_order_confirm("Хорошо, оформляем заказ!") is True


def test_contains_order_confirm_partial():
    assert _contains_order_confirm("Давайте оформим заказ на эту модель") is True


def test_contains_order_confirm_negative():
    assert _contains_order_confirm("Покажите мне фото") is False


def test_strip_order_confirm():
    result = _strip_order_confirm("Хорошо, оформляем заказ! Подскажите размер.")
    assert "оформляем заказ" not in result.lower()
    assert "Подскажите размер" in result


def test_strip_order_confirm_empty():
    assert _strip_order_confirm("") == ""


def test_strip_order_confirm_only_confirm():
    result = _strip_order_confirm("Хорошо, оформляем заказ!")
    assert result  # не пустая, возвращает fallback


# ── _question_for_missing ────────────────────────────────────────────────────


def test_question_city():
    q = _question_for_missing("city")
    assert "город" in q.lower()


def test_question_size():
    q = _question_for_missing("size")
    assert "размер" in q.lower()


def test_question_address():
    q = _question_for_missing("address")
    assert "адрес" in q.lower()


def test_question_unknown_field():
    q = _question_for_missing("unknown")
    assert q  # не пустая


# ── _has_question ────────────────────────────────────────────────────────────


def test_has_question_yes():
    assert _has_question("Какой размер?") is True


def test_has_question_no():
    assert _has_question("Вот ваши фото") is False


def test_has_question_empty():
    assert _has_question("") is False


# ── _asks_for_field / _assistant_already_requests_missing ────────────────────


def test_asks_for_city():
    assert _asks_for_field("Из какого вы города?", "city") is True


def test_asks_for_size():
    assert _asks_for_field("Подскажите размер", "size") is True


def test_asks_not_matching():
    assert _asks_for_field("Вот фото модели", "city") is False


def test_assistant_already_requests_missing():
    text = "Отличный выбор! Подскажите, какой размер вам нужен?"
    assert _assistant_already_requests_missing(text, ["size", "address"]) is True


def test_assistant_not_requesting_missing():
    text = "Вот фото этой модели"
    assert _assistant_already_requests_missing(text, ["size"]) is False


# ── _get_product_color_overrides ─────────────────────────────────────────────


def test_color_overrides_chanel_jumbo():
    result = _get_product_color_overrides("Chanel Jumbo Classic Flap")
    assert "черные" in result


def test_color_overrides_no_match():
    result = _get_product_color_overrides("Jimmy Choo Azia")
    assert result == set()


def test_color_overrides_empty():
    result = _get_product_color_overrides("")
    assert result == set()


# ── _sanitize_order_context ──────────────────────────────────────────────────


def test_sanitize_strips_whitespace():
    ctx = {"city": "  Алматы  ", "product": " Chanel ", "product_type": "bag", "size": "", "color": "", "address": ""}
    result = _sanitize_order_context(ctx)
    assert result["city"] == "Алматы"
    assert result["product"] == "Chanel"


def test_sanitize_normalizes_product_type():
    ctx = {"city": "", "product": "", "product_type": "обувь", "size": "", "color": "", "address": ""}
    result = _sanitize_order_context(ctx)
    assert result["product_type"] == "shoes"


def test_sanitize_handles_none_values():
    ctx = {"city": None, "product": None, "product_type": None, "size": None, "color": None, "address": None}
    result = _sanitize_order_context(ctx)
    assert all(v == "" for v in result.values())
