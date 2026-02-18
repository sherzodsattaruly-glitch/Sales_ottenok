"""
Чистая бизнес-логика управления заказами.
Все функции синхронные, без I/O — можно тестировать без моков.
"""

import re

from gdrive.photo_mapper import tokenize_text

# ── Константы ─────────────────────────────────────────────────────────────────

_ORDER_CONFIRM_TEXT = "Хорошо, оформляем заказ"
_SIZE_REQUIRED_TYPES = {"shoes"}
_ORDER_INTENT_PATTERNS = [
    "оформ", "заказ", "беру", "возьму", "покуп", "куплю", "зафикс", "адрес доставки",
    "давай",  # "давайте их", "давайте эту"
]
_CHECKOUT_HINTS = [
    "зафикс", "оформить заказ", "оформляем заказ", "адрес доставки", "напишите, пожалуйста, адрес", "куда отправ",
]
_FIELD_PROMPT_HINTS = {
    "city": ["город", "из какого", "откуда"],
    "product": ["какую модель", "какой товар", "что оформляем"],
    "size": ["размер"],
    "color": ["цвет", "расцветк"],
    "address": ["адрес", "улиц", "дом", "кварти"],
}
_PRODUCT_COLOR_OVERRIDES = {
    "chanel jumbo classic flap": {"черные"},
    "шанель джумбо": {"черные"},
    "шанел джумбо": {"черные"},
}

# ── Функции ───────────────────────────────────────────────────────────────────


def _normalize_product_type(value: str) -> str:
    v = (value or "").strip().lower()
    if v in {"shoes", "обувь", "shoe"}:
        return "shoes"
    if v in {"bag", "bags", "сумка", "сумки"}:
        return "bag"
    if v in {"accessory", "accessories", "аксессуар", "аксессуары"}:
        return "accessory"
    if v in {"other", "другое"}:
        return "other"
    return ""


def _infer_product_type_from_text(text: str) -> str:
    t = (text or "").lower()
    if "👠" in (text or "") or "👟" in (text or ""):
        return "shoes"
    if "👜" in (text or ""):
        return "bag"
    if any(x in t for x in [
        "туф", "крос", "ботин", "лофер", "балетк", "обув", "каблук", "лодоч",
        "slingback", "джимми чу", "jimmy choo", "saeda", "azia", "opyum", "опиум",
        "sneaker", "кед",
    ]):
        return "shoes"
    if any(x in t for x in [
        "сумк", "bag", "chanel 25", "arcadie", "pochette", "flap",
        "кошелек", "кошелёк", "wallet", "monogram", "jumbo",
    ]):
        return "bag"
    return ""


def _sanitize_order_context(ctx: dict) -> dict:
    return {
        "city": (ctx.get("city") or "").strip(),
        "product": (ctx.get("product") or "").strip(),
        "product_type": _normalize_product_type(ctx.get("product_type") or ""),
        "size": (ctx.get("size") or "").strip(),
        "color": (ctx.get("color") or "").strip(),
        "address": (ctx.get("address") or "").strip(),
    }


def _merge_order_context(base: dict, updates: dict) -> dict:
    merged = _sanitize_order_context(base)
    incoming = _sanitize_order_context(updates)

    # Detect product switch — reset dependent fields
    if incoming.get("product") and merged.get("product"):
        old_tokens = tokenize_text(merged["product"])
        new_tokens = tokenize_text(incoming["product"])
        if old_tokens and new_tokens:
            overlap = old_tokens & new_tokens
            similarity = len(overlap) / max(len(old_tokens), len(new_tokens))
            if similarity < 0.5:
                merged["size"] = ""
                merged["color"] = ""
                merged["address"] = ""

    for key in ("city", "product", "size", "color", "address"):
        if incoming.get(key):
            merged[key] = incoming[key]
    if incoming.get("product_type"):
        merged["product_type"] = incoming["product_type"]
    if not merged.get("product_type"):
        merged["product_type"] = _infer_product_type_from_text(merged.get("product", ""))
    return merged


def _contains_order_confirm(text: str) -> bool:
    t = (text or "").lower()
    if "хорошо, оформляем заказ" in t or "хорошо оформляем заказ" in t:
        return True
    if "оформ" in t and "заказ" in t:
        return True
    return re.search(r"оформ\w*\s+заказ", t) is not None


_ORDER_CONFIRM_RE = re.compile(
    r"(?i)\bоформ\w*\s+заказ|\bоформляем\s+заказ|\bоформим\s+заказ|\bхорошо,?\s*оформ",
)


def _strip_order_confirm(text: str) -> str:
    """Remove parts that contain order confirmation phrases.

    Works on ||| -separated parts: drops any short part (<150 chars) that
    matches an order-confirm pattern.  For longer parts the matching
    *sentence* is removed so surrounding text is preserved intact.
    """
    if not text:
        return text
    parts = [p.strip() for p in text.split("|||") if p.strip()]
    kept: list[str] = []
    for part in parts:
        if not _ORDER_CONFIRM_RE.search(part):
            kept.append(part)
            continue
        # Short part with order confirm → drop entirely
        if len(part) < 150:
            continue
        # Long part → remove only the sentence containing the phrase
        sentences = re.split(r"(?<=[.!?])\s+", part)
        clean_sentences = [s for s in sentences if not _ORDER_CONFIRM_RE.search(s)]
        if clean_sentences:
            kept.append(" ".join(clean_sentences))
    result = "|||".join(kept).strip()
    return result or "Сейчас уточню детали заказа."


def _build_missing_fields(order_ctx: dict, color_required: bool) -> list[str]:
    missing = []
    # Сначала собираем основные данные в правильном порядке
    if not order_ctx.get("city"):
        missing.append("city")
    if not order_ctx.get("product"):
        missing.append("product")
    if order_ctx.get("product_type") in _SIZE_REQUIRED_TYPES and not order_ctx.get("size"):
        missing.append("size")
    if color_required and not order_ctx.get("color"):
        missing.append("color")

    # Адрес запрашиваем ТОЛЬКО после того, как собраны все основные данные
    # (город, товар, размер если нужен, цвет если нужен)
    basic_fields_collected = (
        order_ctx.get("city")
        and order_ctx.get("product")
        and (order_ctx.get("product_type") not in _SIZE_REQUIRED_TYPES or order_ctx.get("size"))
        and (not color_required or order_ctx.get("color"))
    )

    if basic_fields_collected and not order_ctx.get("address"):
        missing.append("address")

    return missing


def _question_for_missing(field: str) -> str:
    if field == "city":
        return "Подскажите, пожалуйста, из какого вы города?"
    if field == "product":
        return "Уточните, пожалуйста, какую модель оформляем?"
    if field == "size":
        return "Подскажите, пожалуйста, какой размер вам нужен?"
    if field == "color":
        return "Подскажите, пожалуйста, какой цвет выбираете?"
    if field == "address":
        return "Напишите, пожалуйста, адрес доставки?"
    return "Подскажите, пожалуйста, недостающие данные для оформления заказа?"


def _has_question(text: str) -> bool:
    return "?" in (text or "")


def _has_order_intent(text: str) -> bool:
    t = (text or "").lower()
    return any(p in t for p in _ORDER_INTENT_PATTERNS)


def _asks_for_field(text: str, field: str) -> bool:
    t = (text or "").lower()
    hints = _FIELD_PROMPT_HINTS.get(field, [])
    return any(h in t for h in hints)


def _assistant_already_requests_missing(text: str, missing_fields: list[str]) -> bool:
    return any(_asks_for_field(text, f) for f in missing_fields)


def _strip_checkout_prompts(text: str) -> str:
    if not text:
        return text
    parts = [p.strip() for p in text.split("|||") if p.strip()]
    kept = []
    for p in parts:
        low = p.lower()
        if len(p) < 120 and any(h in low for h in _CHECKOUT_HINTS):
            continue
        kept.append(p)
    if not kept:
        return ""
    return "|||".join(kept)


_ORDER_CONFIRMATION_PATTERNS = [
    "да", "верно", "всё верно", "все верно", "правильно", "всё правильно",
    "все правильно", "подтверждаю", "оформляйте", "оформляй", "ок", "ok",
    "yes", "угу", "ага", "точно", "да, верно", "да, всё верно", "да, все верно",
    "да, оформляйте", "да оформляйте", "да, правильно",
]


def _is_order_confirmation(text: str) -> bool:
    """Проверить, подтверждает ли клиент заказ."""
    t = (text or "").strip().lower()
    if not t:
        return False
    # Точное совпадение или совпадение с пунктуацией (да!, ок., верно!)
    cleaned = re.sub(r'[!.,?]+$', '', t).strip()
    return cleaned in _ORDER_CONFIRMATION_PATTERNS


_NEGATIVE_PATTERNS = {
    "нет", "не нужно", "не надо", "не хочу", "спасибо нет",
    "подумаю", "посмотрим", "может позже", "пока нет", "не сейчас",
    "передумал", "передумала", "воздержусь", "пока",
}
_NEGATIVE_SUBSTRINGS = ["подумаю", "посмотрим", "позже", "потом", "когда-нибудь", "пока нет"]


def _is_negative_or_undecided(text: str) -> bool:
    """Проверить, отказывается ли клиент или откладывает решение."""
    t = re.sub(r'[!.,?]+$', '', (text or "").strip().lower()).strip()
    return t in _NEGATIVE_PATTERNS or any(p in t for p in _NEGATIVE_SUBSTRINGS)


def _build_item_desc(order_ctx: dict) -> str:
    """Сформировать краткое описание товара из контекста заказа для сообщений."""
    parts = []
    if order_ctx.get("product"):
        parts.append(order_ctx["product"])
    if order_ctx.get("color"):
        parts.append(order_ctx["color"])
    if order_ctx.get("size") and order_ctx.get("product_type") in _SIZE_REQUIRED_TYPES:
        parts.append(f"{order_ctx['size']} размера")
    return " ".join(parts) if parts else "этот товар"


def _build_order_summary(order_ctx: dict) -> str:
    """Сформировать текстовую сводку заказа для подтверждения клиентом."""
    lines = ["Ваш заказ:"]
    if order_ctx.get("product"):
        lines.append(f"Товар: {order_ctx['product']}")
    if order_ctx.get("product_type") in _SIZE_REQUIRED_TYPES and order_ctx.get("size"):
        lines.append(f"Размер: {order_ctx['size']}")
    if order_ctx.get("color"):
        lines.append(f"Цвет: {order_ctx['color']}")
    if order_ctx.get("city"):
        lines.append(f"Город: {order_ctx['city']}")
    if order_ctx.get("address"):
        lines.append(f"Адрес: {order_ctx['address']}")
    summary = "\n".join(lines)
    return f"{summary}|||Проверьте, пожалуйста, всё верно? Оформляем?"


def _get_product_color_overrides(product_name: str) -> set[str]:
    product = (product_name or "").strip().lower()
    if not product:
        return set()
    result = set()
    for pattern, colors in _PRODUCT_COLOR_OVERRIDES.items():
        if pattern in product:
            result.update(colors)
    return result
