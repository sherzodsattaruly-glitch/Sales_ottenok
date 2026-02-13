"""
AI-РѕСЂРєРµСЃС‚СЂР°С‚РѕСЂ: СЃРІСЏР·С‹РІР°РµС‚ RAG, GPT Рё Google Drive.
Р“Р»Р°РІРЅР°СЏ С‚РѕС‡РєР° РѕР±СЂР°Р±РѕС‚РєРё СЃРѕРѕР±С‰РµРЅРёР№.
"""

import asyncio
import json
import logging
import re

from openai import AsyncOpenAI

from ai.prompts import SYSTEM_PROMPT
from ai.rag import search_products, search_scripts
from db.conversations import (
    get_conversation_history,
    save_message,
    has_sent_product_photos,
    mark_product_photos_sent,
    get_handoff_state,
    set_handoff_state,
    get_order_context,
    upsert_order_context,
    reset_nudge_state,
    update_last_client_message,
)
from gdrive.photo_mapper import find_product_photos, tokenize_text, select_photos_with_color_variety
from inventory.stock_checker import check_product_availability, format_availability_message
from greenapi.client import send_text, send_multiple_images
from config import (
    OPENAI_API_KEY,
    OPENAI_MODEL,
    MAX_PHOTOS_PER_MESSAGE,
    MAX_PHOTOS_PRODUCT_SHOWCASE,
    MAX_PHOTOS_PER_COLOR,
    MANAGER_CHAT_IDS,
)

logger = logging.getLogger(__name__)

openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

_PHOTO_REQUEST_PATTERNS = [
    "фото", "фотку", "фотки", "фотографию", "фотографию", "снимок",
    "покажи", "покажите", "покажешь", "показать", "посмотреть",
    "скинь", "скиньте", "пришли", "пришлите", "кинь", "киньте",
    "отправь", "отправьте",
]

_PRODUCT_HINT_TOKENS = {
    "chanel", "шанел", "шанель", "miu", "miu miu", "джимми", "jimmy", "choo",
    "gucci", "dior", "saint", "laurent", "golden", "goose", "jimbo", "джумбо",
    "classic", "flap", "arcadie", "azia", "saeda", "opyum", "25", "yves",
}
_PRODUCT_RAW_HINTS = [
    "шанел", "chanel", "джумбо", "jumbo", "классик", "classic", "flap",
    "джимми", "jimmy", "чу", "choo", "саеда", "saeda", "азия", "azia",
    "миу", "miu", "arcadie", "слингбэк", "slingback",
]


def _is_photo_request(text: str) -> bool:
    t = text.lower()
    if any(p in t for p in _PHOTO_REQUEST_PATTERNS):
        return True
    if re.search(r"как\s+он\s+выгляд", t):
        return True
    if re.search(r"как\s+выглядит", t):
        return True
    return False


def _build_product_key(user_tokens: set[str], photos: list[dict]) -> str:
    if photos:
        tokens = tokenize_text(photos[0].get("filename", ""))
    else:
        tokens = set(user_tokens)
    tokens = [t for t in tokens if t]
    if not tokens:
        return ""
    return "|".join(sorted(tokens))


def _should_use_active_product_query(user_message: str, active_product: str) -> bool:
    if not active_product:
        return False
    user_tokens = tokenize_text(user_message)
    product_tokens = tokenize_text(active_product)
    if user_tokens & product_tokens:
        return False
    text_l = user_message.lower()
    if any(h in text_l for h in _PRODUCT_RAW_HINTS):
        return False
    # If user explicitly names another product/brand, keep current message as query.
    if any(tok in _PRODUCT_HINT_TOKENS for tok in user_tokens):
        return False
    return True


def _dedupe_photos(photos: list[dict]) -> list[dict]:
    seen = set()
    result = []
    for p in photos:
        key = p.get("file_id") or p.get("filename") or p.get("direct_url")
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(p)
    return result


def _normalize_photo_captions(photos: list[dict]) -> list[dict]:
    """Оставляет подпись у каждой фотографии (название модели)."""
    if not photos:
        return photos
    normalized = []
    for p in photos:
        item = dict(p)
        # Больше не очищаем подпись для последующих фото, как просил пользователь
        normalized.append(item)
    return normalized


def _extract_chat_id(text: str) -> str:
    # Prefer explicit chatId
    m = re.search(r"(\d{8,15})@c\.us", text)
    if m:
        return f"{m.group(1)}@c.us"
    # Fallback to digits
    digits = re.findall(r"\d{8,15}", text)
    if digits:
        return f"{digits[-1]}@c.us"
    # Fallback: join all digits (handles spaces in phone numbers)
    digits_all = re.sub(r"\D", "", text)
    if 8 <= len(digits_all) <= 15:
        return f"{digits_all}@c.us"
    return ""


def _parse_handoff_command(text: str) -> tuple[str | None, str | None]:
    t = text.strip().lower()
    if not (t.startswith("/handoff") or t.startswith("handoff") or t.startswith("/human") or t.startswith("human")):
        return None, None
    if " on" in t or t.endswith(" on"):
        action = "on"
    elif " off" in t or t.endswith(" off"):
        action = "off"
    elif " status" in t or t.endswith(" status"):
        action = "status"
    else:
        action = None
    target = _extract_chat_id(text)
    return action, target


_GREETING_WORDS = ["здравствуйте", "привет", "добрый день", "добрый вечер", "доброе утро"]

_ORDER_CONFIRM_TEXT = "Хорошо, оформляем заказ"
_SIZE_REQUIRED_TYPES = {"shoes", "clothes"}
_COLOR_REQUIREMENT_CACHE: dict[str, bool] = {}
_ORDER_INTENT_PATTERNS = [
    "оформ", "заказ", "беру", "возьму", "покуп", "куплю", "зафикс", "адрес",
]
_CHECKOUT_HINTS = [
    "зафикс", "оформ", "адрес доставки", "напишите, пожалуйста, адрес", "куда отправ",
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
_AVAILABILITY_HINTS = [
    "есть", "имеется", "в наличии", "бывает", "были", "будет",
]
_MODEL_QUERY_IGNORE_TOKENS = {
    "есть", "какой", "какая", "какие", "нужен", "нужна", "нужны",
    "покажи", "показать", "пришли", "скинь", "модель", "модели",
    "цвет", "размер", "размеры", "город", "адрес", "сумка", "сумки", "туфли",
    "обувь", "аксессуар", "аксессуары", "в", "на", "и", "или",
    "еще", "ещё", "цена", "цены", "сколько", "стоит", "наличии", "наличие",
    "кроссовки", "кросовки", "кеды", "балетки", "лоферы", "слингбэки", "слингбэк",
    "chanel", "saint", "laurent", "ysl", "yves", "jimmy", "choo", "miu",
    "louis", "vuitton", "gucci", "dior", "golden", "goose",
    "ив", "сан", "лоран", "сен", "шанел", "шанель", "джими", "джимми", "чу",
}
_TYPE_FALLBACK_ALTERNATIVES = {
    "shoes": ["Golden Goose Super-Star", "Saint Laurent Opyum", "Chanel Classic Slingbacks", "Jimmy Choo Azia 95"],
    "bag": ["Chanel Jumbo Classic Flap", "Yves Saint Laurent Monogram", "Louis Vuitton Pochette Felicie", "Miu Miu Arcadie", "Miu Miu Wander"],
}


def _normalize_product_type(value: str) -> str:
    v = (value or "").strip().lower()
    if v in {"shoes", "обувь", "shoe"}:
        return "shoes"
    if v in {"clothes", "одежда", "clothing"}:
        return "clothes"
    if v in {"bag", "bags", "сумка", "сумки"}:
        return "bag"
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
    if any(x in t for x in ["плать", "юбк", "куртк", "пальт", "брюк", "джинс", "футболк", "одежд"]):
        return "clothes"
    if any(x in t for x in [
        "сумк", "bag", "chanel 25", "arcadie", "pochette", "flap",
        "кошелек", "кошелёк", "wallet", "monogram", "jumbo",
    ]):
        return "bag"
    return ""


def _extract_product_name_from_result(result: dict) -> str:
    meta = (result.get("metadata") or {})
    name = (meta.get("product_name") or "").strip()
    if name:
        return name
    text = (result.get("text") or "").strip()
    if not text:
        return ""
    m = re.search(r"[👠👟👜]\s*([^\n]+)", text)
    candidate = (m.group(1) if m else text.splitlines()[0]).strip()
    candidate = re.split(r"\s+[—-]\s+", candidate)[0].strip()
    candidate = re.sub(r"\s{2,}.*$", "", candidate).strip()
    candidate = candidate[:120]
    return candidate if _looks_like_product_name(candidate) else ""


def _infer_result_product_type(result: dict) -> str:
    name = _extract_product_name_from_result(result)
    text = (result.get("text") or "")[:260]
    return _infer_product_type_from_text(f"{name} {text}")


def _looks_like_product_name(name: str) -> bool:
    n = (name or "").strip()
    if not n:
        return False
    low = n.lower()
    if any(bad in low for bad in ["именно по", "описание", "цены", "вместе с ценой", "приветствие"]):
        return False
    if ":" in n and not any(h in n.lower() for h in ["chanel", "saint", "laurent", "jimmy", "miu", "louis", "golden"]):
        return False
    words = re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9]+", n.lower())
    if len(words) > 8:
        return False
    tokens = tokenize_text(n)
    if tokens & _PRODUCT_HINT_TOKENS:
        return True
    if any(b in n.lower() for b in ["chanel", "saint", "laurent", "jimmy", "miu", "louis", "golden", "ysl"]):
        return True
    return False


def _filter_photos_by_requested_type(photos: list[dict], requested_type: str) -> list[dict]:
    if not photos or not requested_type:
        return photos
    matched = []
    for p in photos:
        filename = p.get("filename", "")
        p_type = _infer_product_type_from_text(filename)
        if p_type == requested_type:
            matched.append(p)
    return matched


def _build_fallback_photo_queries(user_message: str, requested_type: str) -> list[str]:
    t = (user_message or "").lower()
    queries: list[str] = []
    if requested_type == "shoes":
        if any(x in t for x in ["сан лоран", "ив сан", "saint laurent", "ysl"]):
            queries.append("Saint Laurent Opyum")
    if requested_type == "bag":
        if any(x in t for x in ["сан лоран", "ив сан", "saint laurent", "ysl"]):
            queries.append("Yves Saint Laurent Monogram")
    return queries


def _is_availability_request(text: str) -> bool:
    t = (text or "").lower()
    return any(h in t for h in _AVAILABILITY_HINTS)


def _extract_specific_query_tokens(text: str) -> set[str]:
    tokens = tokenize_text(text or "")
    specific = set()
    for tok in tokens:
        if not tok or tok.isdigit() or len(tok) < 3:
            continue
        if tok in _MODEL_QUERY_IGNORE_TOKENS:
            continue
        if tok in _COLOR_PREFIXES.values():
            continue
        if any(tok.startswith(prefix) for prefix in _COLOR_PREFIXES):
            continue
        specific.add(tok)
    return specific


def _match_name_overlap(query_text: str, product_name: str) -> int:
    q = tokenize_text(query_text or "")
    p = tokenize_text(product_name or "")
    return len(q & p)


def _pick_primary_product_match(product_results: list[dict], query_text: str) -> str:
    best_name = ""
    best_score = -1
    for r in product_results:
        name = _extract_product_name_from_result(r)
        if not name:
            continue
        score = _match_name_overlap(query_text, name)
        if score > best_score:
            best_score = score
            best_name = name
    return best_name


def _collect_similar_product_names(
    product_results: list[dict],
    requested_type: str = "",
    exclude_names: set[str] | None = None,
    limit: int = 3,
) -> list[str]:
    excluded = {(x or "").strip().lower() for x in (exclude_names or set()) if x}
    names: list[str] = []
    seen = set()
    for r in product_results:
        name = _extract_product_name_from_result(r)
        if not name:
            continue
        name_l = name.lower()
        if name_l in seen or name_l in excluded:
            continue
        if requested_type:
            r_type = _infer_result_product_type(r)
            if r_type and r_type != requested_type:
                continue
        seen.add(name_l)
        names.append(name)
        if len(names) >= limit:
            break
    return names


def _append_similar_products_text(base_text: str, similar_names: list[str]) -> str:
    if not similar_names:
        return base_text
    variants = "; ".join(similar_names)
    return f"{base_text}|||Похожие варианты: {variants}. Какой вариант показать?"


def _fallback_alternative_names(product_type: str, exclude_names: set[str] | None = None, limit: int = 3) -> list[str]:
    excluded = {(x or "").strip().lower() for x in (exclude_names or set()) if x}
    candidates = _TYPE_FALLBACK_ALTERNATIVES.get(product_type or "", [])
    result = []
    for name in candidates:
        if name.lower() in excluded:
            continue
        result.append(name)
        if len(result) >= limit:
            break
    return result


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


def _strip_order_confirm(text: str) -> str:
    if not text:
        return text
    cleaned = re.sub(
        r"(?i)\bхорошо,?\s*оформляем\s*заказ\b[.!]?",
        "",
        text,
    )
    cleaned = re.sub(
        r"(?i)\bоформ\w*\s+заказ\b[.!]?",
        "",
        cleaned,
    )
    cleaned = re.sub(r"(?i)\bоформим\s+заказ\b[.!]?", "", cleaned)
    cleaned = re.sub(r"(?i)\bоформляем\s+заказ\b[.!]?", "", cleaned)
    cleaned = re.sub(r"\|\|\|\s*\|\|\|", "|||", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" |")
    return cleaned.strip() or "Сейчас уточню детали заказа."


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
        if any(h in low for h in _CHECKOUT_HINTS):
            continue
        kept.append(p)
    if not kept:
        return ""
    return "|||".join(kept)


def _dedupe_response_parts(text: str) -> str:
    if not text:
        return text
    parts = [p.strip() for p in text.split("|||") if p.strip()]
    if not parts:
        return text
    seen = set()
    kept = []
    for part in parts:
        key = re.sub(r"\s+", " ", part.lower())
        key = re.sub(r"[^\w\sа-яё]", "", key)
        if key in seen:
            continue
        seen.add(key)
        kept.append(part)
    return "|||".join(kept)


def _get_product_color_overrides(product_name: str) -> set[str]:
    product = (product_name or "").strip().lower()
    if not product:
        return set()
    result = set()
    for pattern, colors in _PRODUCT_COLOR_OVERRIDES.items():
        if pattern in product:
            result.update(colors)
    return result


def _format_color_unavailable_message(product_name: str, requested_color: str, available_colors: set[str]) -> str:
    product = product_name or "этой модели"
    if available_colors:
        colors_text = ", ".join(sorted(available_colors))
        if len(available_colors) == 1:
            only_color = next(iter(available_colors))
            return (
                f"По модели {product} цвета {requested_color} сейчас нет. "
                f"Есть только {only_color}. Подойдет этот вариант?"
            )
        return (
            f"По модели {product} цвета {requested_color} сейчас нет. "
            f"Доступные цвета: {colors_text}. Какой цвет выбираете?"
        )
    return (
        f"По модели {product} цвет {requested_color} сейчас не вижу в наличии. "
        "Подскажите, пожалуйста, какой цвет рассмотрим из доступных?"
    )


def _format_order_context_for_prompt(order_ctx: dict, missing_fields: list[str], color_required: bool) -> str:
    fields_ru = {
        "city": "город",
        "product": "товар",
        "size": "размер",
        "color": "цвет",
        "address": "адрес",
    }
    missing_ru = ", ".join(fields_ru[f] for f in missing_fields) if missing_fields else "нет"
    return (
        "КОНТЕКСТ ЗАКАЗА:\n"
        f"- город: {order_ctx.get('city') or '-'}\n"
        f"- товар: {order_ctx.get('product') or '-'}\n"
        f"- тип товара: {order_ctx.get('product_type') or '-'}\n"
        f"- размер: {order_ctx.get('size') or '-'}\n"
        f"- цвет: {order_ctx.get('color') or '-'}\n"
        f"- адрес: {order_ctx.get('address') or '-'}\n"
        f"- цвет обязателен: {'да' if color_required else 'нет'}\n"
        f"- недостающие поля: {missing_ru}\n"
        "ПРАВИЛО: фразу 'Хорошо, оформляем заказ' можно писать только когда недостающих полей нет."
    )


async def _extract_order_fields(user_message: str, history: list[dict], current_ctx: dict) -> dict:
    history_text = "\n".join(
        f"{m.get('role')}: {m.get('content')}" for m in history[-8:]
    )
    system_text = (
        "Извлеки данные заказа из сообщения клиента. Верни только JSON.\n"
        "Поля JSON: city, product, product_type, size, color, address, ready_to_order.\n"
        "product_type только: shoes, clothes, bag, other, unknown.\n"
        "Если поле неизвестно, возвращай пустую строку.\n"
        "ready_to_order = true только если клиент явно готов оформить/купить."
    )
    user_text = (
        f"Текущее сообщение: {user_message}\n"
        f"Контекст профиля: {json.dumps(current_ctx, ensure_ascii=False)}\n"
        f"История: {history_text}"
    )
    try:
        completion = await openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            temperature=0,
            max_tokens=220,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_text},
                {"role": "user", "content": user_text},
            ],
        )
        raw = completion.choices[0].message.content or "{}"
        parsed = json.loads(raw)
        return {
            "city": parsed.get("city") or "",
            "product": parsed.get("product") or "",
            "product_type": parsed.get("product_type") or "",
            "size": parsed.get("size") or "",
            "color": parsed.get("color") or "",
            "address": parsed.get("address") or "",
            "ready_to_order": bool(parsed.get("ready_to_order", False)),
        }
    except Exception as e:
        logger.warning(f"Failed to extract order fields: {e}")
        return {
            "city": "",
            "product": "",
            "product_type": "",
            "size": "",
            "color": "",
            "address": "",
            "ready_to_order": False,
        }


def _strip_duplicate_greeting(text: str, history: list[dict]) -> str:
    """Убираем приветствие из ответа GPT, если бот уже здоровался в этой переписке."""
    bot_already_greeted = False
    for m in history:
        if m.get("role") == "assistant":
            content = (m.get("content") or "").lower()
            if any(g in content for g in _GREETING_WORDS):
                bot_already_greeted = True
                break

    if not bot_already_greeted:
        return text

    # Разбиваем по ||| и проверяем первую часть
    parts = [p.strip() for p in text.split("|||") if p.strip()]
    if not parts:
        return text

    first_lower = parts[0].lower().strip()
    # Если первая часть — короткое приветствие (до 30 символов), убираем
    if any(first_lower.startswith(g) for g in _GREETING_WORDS) and len(first_lower) < 30:
        parts = parts[1:]

    if not parts:
        return text

    return "|||".join(parts)


def _caption_from_filename(filename: str) -> str:
    """'кроссовки черные Golden Goose Ball Star.jpg' -> 'Golden Goose Ball Star'"""
    # 1. Удаляем расширение
    name = re.sub(r'\.\w+$', '', filename)
    # 2. Удаляем кириллицу (любые русские слова и характеристики)
    name = re.sub(r'[а-яА-ЯёЁ]+', '', name)
    # 2a. Удаляем лишние знаки препинания, которые могли остаться (запятые, тире по краям)
    name = re.sub(r'^[^\w\d]+|[^\w\d]+$', '', name) # Trim non-alphanumeric from ends
    name = re.sub(r'[,.;:]', ' ', name) # Replace punctuation with spaces
    # 3. Удаляем технические индексы в конце (например, " 1", " 02")
    name = re.sub(r'\s+\d{1,2}$', '', name.strip())
    # 4. Убираем лишние пробелы, которые могли остаться после удаления русских слов
    name = re.sub(r'\s{2,}', ' ', name)
    return name.strip()


# Определение цвета в тексте пользователя
_COLOR_PREFIXES = {
    "розов": "розовые", "pink": "розовые",
    "черн": "черные", "black": "черные",
    "беж": "бежевые", "beige": "бежевые",
    "бел": "белые", "white": "белые",
    "красн": "красные", "red": "красные",
    "синий": "синие", "синих": "синие", "синие": "синие",
    "золот": "золотые", "gold": "золотые",
    "серебр": "серебряные", "silver": "серебряные",
    "коричнев": "коричневые", "brown": "коричневые",
    "зелен": "зеленые", "green": "зеленые",
}


def _detect_color_in_text(text: str) -> str | None:
    """Определить цвет, упомянутый в тексте. Возвращает нормализованный ключ или None."""
    t = text.lower()
    for prefix, color_key in _COLOR_PREFIXES.items():
        if prefix in t:
            return color_key
    return None


def _detect_color_from_filename(filename: str) -> str:
    f = (filename or "").lower()
    for prefix, color_name in _COLOR_PREFIXES.items():
        if prefix in f:
            return color_name
    return ""


async def _is_color_required(product_name: str) -> bool:
    product = (product_name or "").strip().lower()
    if not product:
        return False
    if product in _COLOR_REQUIREMENT_CACHE:
        return _COLOR_REQUIREMENT_CACHE[product]
    try:
        photos = await find_product_photos(product_name=product_name)
        colors = {_detect_color_from_filename(p.get("filename", "")) for p in photos}
        colors.discard("")
        required = len(colors) > 1
        _COLOR_REQUIREMENT_CACHE[product] = required
        return required
    except Exception as e:
        logger.warning(f"Failed to detect color requirement for '{product_name}': {e}")
        return False


async def _get_available_colors_for_product(product_name: str) -> set[str]:
    product = (product_name or "").strip()
    if not product:
        return set()
    try:
        photos = await find_product_photos(product_name=product)
        colors = {_detect_color_from_filename(p.get("filename", "")) for p in photos}
        colors.discard("")
        return colors
    except Exception as e:
        logger.warning(f"Failed to list available colors for '{product_name}': {e}")
        return set()


def _pick_product_photos(found_photos: list[dict], requested_color: str | None = None) -> list[dict]:
    """
    Выбрать фото товара.
    - requested_color задан → отфильтровать по цвету, отдать все подходящие
    - requested_color = None → обзорный режим: по 1 фото каждого цвета
    """
    if requested_color:
        # Конкретный цвет — фильтруем по имени файла и отдаём все
        color_prefixes = [p for p, key in _COLOR_PREFIXES.items() if key == requested_color]
        matching = [
            img for img in found_photos
            if any(cp in img.get("filename", "").lower() for cp in color_prefixes)
        ]
        # Если клиент запросил конкретный цвет и совпадений нет, не отправляем другой цвет.
        source = matching
        source = _dedupe_photos(source)
        return [
            {
                "file_id": p["file_id"],
                "caption": _caption_from_filename(p["filename"]),
                "filename": p["filename"],
            }
            for p in source[:MAX_PHOTOS_PRODUCT_SHOWCASE]
        ]
    else:
        # Обзорный режим — по 1 фото каждого цвета
        picked = select_photos_with_color_variety(
            found_photos,
            max_total=MAX_PHOTOS_PRODUCT_SHOWCASE,
            max_per_color=1,
        )
        picked = _dedupe_photos(picked)
        return [
            {
                "file_id": p["file_id"],
                "caption": _caption_from_filename(p["filename"]),
                "filename": p["filename"],
            }
            for p in picked
        ]


async def generate_response(chat_id: str, user_message: str, sender_name: str) -> dict:
    """
    Р“РµРЅРµСЂРёСЂСѓРµС‚ РѕС‚РІРµС‚ Р±РѕС‚Р°.
    Р’РѕР·РІСЂР°С‰Р°РµС‚: {'text': str, 'photos': list[dict]}
    """
    # 1. РЎРѕС…СЂР°РЅСЏРµРј РІС…РѕРґСЏС‰РµРµ СЃРѕРѕР±С‰РµРЅРёРµ
    await save_message(chat_id, "user", user_message, sender_name)

    # Сбрасываем дожим когда клиент отвечает
    await reset_nudge_state(chat_id)

    # Предварительно читаем контекст заказа, чтобы не терять активный товар
    current_order_ctx = await get_order_context(chat_id)
    requested_product_type = _infer_product_type_from_text(user_message)
    product_query = user_message
    if _should_use_active_product_query(user_message, current_order_ctx.get("product", "")):
        product_query = current_order_ctx.get("product", "") or user_message

    # 2. РџР°СЂР°Р»Р»РµР»СЊРЅРѕ РёС‰РµРј РІ Р±Р°Р·Рµ Р·РЅР°РЅРёР№
    product_results, script_results = await asyncio.gather(
        search_products(product_query),
        search_scripts(user_message),
    )
    if requested_product_type:
        filtered_results = []
        for r in product_results:
            result_type = _infer_result_product_type(r)
            if not result_type or result_type == requested_product_type:
                filtered_results.append(r)
        product_results = filtered_results
    primary_product_match = _pick_primary_product_match(product_results, user_message)
    specific_query_tokens = _extract_specific_query_tokens(user_message)

    # 3. РЎРѕР±РёСЂР°РµРј РєРѕРЅС‚РµРєСЃС‚С‹
    product_context = "\n---\n".join([r["text"] for r in product_results])
    product_context = product_context or "РќРµС‚ СЂРµР»РµРІР°РЅС‚РЅС‹С… С‚РѕРІР°СЂРѕРІ РІ Р±Р°Р·Рµ."

    sales_context = "\n---\n".join([r["text"] for r in script_results])
    sales_context = sales_context or "РќРµС‚ СЂРµР»РµРІР°РЅС‚РЅС‹С… СЃРєСЂРёРїС‚РѕРІ."

    # 4. РСЃС‚РѕСЂРёСЏ РїРµСЂРµРїРёСЃРєРё
    history = await get_conversation_history(chat_id)
    is_new_client = len(history) <= 1
    history_text = "\n".join(
        [f"{'Клиент' if m['role'] == 'user' else 'Алина'}: {m['content']}" for m in history]
    )

    order_ctx = current_order_ctx
    extracted_fields = await _extract_order_fields(user_message, history, order_ctx)
    llm_ready_to_order = bool(extracted_fields.get("ready_to_order", False))

    rag_product_name = ""
    if product_results:
        rag_product_name = _extract_product_name_from_result(product_results[0]) or ""
    target_product_type = requested_product_type or _infer_product_type_from_text(primary_product_match or rag_product_name)
    similar_product_names = _collect_similar_product_names(
        product_results,
        requested_type=target_product_type,
        exclude_names={primary_product_match} if primary_product_match else set(),
        limit=3,
    )

    if rag_product_name and not extracted_fields.get("product") and not order_ctx.get("product"):
        extracted_fields["product"] = rag_product_name
    if not extracted_fields.get("product_type"):
        extracted_fields["product_type"] = _infer_product_type_from_text(
            extracted_fields.get("product") or rag_product_name
        )
    if target_product_type:
        extracted_fields["product_type"] = target_product_type

    order_ctx = _merge_order_context(order_ctx, extracted_fields)
    if not order_ctx.get("product") and rag_product_name:
        order_ctx["product"] = rag_product_name
    if not order_ctx.get("product_type"):
        order_ctx["product_type"] = _infer_product_type_from_text(order_ctx.get("product", ""))

    await upsert_order_context(chat_id, order_ctx)
    color_required = await _is_color_required(order_ctx.get("product", ""))
    missing_order_fields = _build_missing_fields(order_ctx, color_required)
    order_guard_prompt = _format_order_context_for_prompt(order_ctx, missing_order_fields, color_required)

    system_prompt = SYSTEM_PROMPT.format(
        product_context=product_context,
        sales_context=sales_context,
        conversation_history=history_text,
    ) + "\n\n" + order_guard_prompt

    # 6. Р’С‹Р·С‹РІР°РµРј GPT
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    completion = await openai_client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=messages,
        temperature=0.7,
        max_tokens=700,
    )

    assistant_text = completion.choices[0].message.content

    # 7a. Убираем повторное приветствие на уровне кода
    assistant_text = _strip_duplicate_greeting(assistant_text, history)
    user_order_intent = _has_order_intent(user_message)
    # Не считаем заказ "готовым" только по предположению LLM без явного сигнала клиента.
    ready_to_order = user_order_intent
    address_just_collected = bool((extracted_fields.get("address") or "").strip())
    if not user_order_intent:
        assistant_text = _strip_checkout_prompts(assistant_text) or "Сейчас уточню по модели и наличию."

    # 7b. Жесткая проверка: до сбора всех данных заказ не оформляем
    if missing_order_fields:
        if _contains_order_confirm(assistant_text):
            assistant_text = _strip_order_confirm(assistant_text)
        # Задаем вопрос о недостающих полях если:
        # 1. Клиент хочет заказать ИЛИ только что дали адрес (как раньше)
        # 2. ИЛИ товар определен в order_ctx (клиент интересуется товаром)
        # 3. НО НЕ при первом приветствии (is_new_client) - тогда промпт сам задаст вопрос
        should_force_missing_question = (
            not is_new_client  # Добавлена проверка: не задаем доп. вопросы при первом контакте
            and (
                ready_to_order
                or address_just_collected
                or bool(order_ctx.get("product"))
            )
        )
        if should_force_missing_question and not _assistant_already_requests_missing(assistant_text, missing_order_fields) and not _has_question(assistant_text):
            assistant_text = f"{assistant_text}|||{_question_for_missing(missing_order_fields[0])}".strip("|")
    elif (ready_to_order or address_just_collected or llm_ready_to_order) and not _contains_order_confirm(assistant_text):
        # 7c. Проверка наличия товара перед оформлением заказа
        product_name = order_ctx.get("product", "")
        size = order_ctx.get("size", "")
        color = order_ctx.get("color", "")

        if product_name:
            try:
                availability = check_product_availability(product_name, size, color)

                if not availability["available"]:
                    # Товара нет в наличии - не оформляем заказ
                    logger.info(
                        "Товар '%s' (size=%s, color=%s) нет в наличии",
                        product_name, size, color
                    )
                    assistant_text = format_availability_message(availability, product_name)

                    # Предлагаем альтернативы если есть
                    if similar_product_names:
                        alternatives_text = "Могу предложить похожие варианты: " + ", ".join(similar_product_names)
                        assistant_text = f"{assistant_text}|||{alternatives_text}".strip("|")

                else:
                    # Товар в наличии - можно оформлять заказ
                    logger.info(
                        "Товар '%s' (size=%s, color=%s) в наличии: quantity=%d, price=%s",
                        product_name, size, color,
                        availability["quantity"], availability["price"]
                    )
                    availability_msg = format_availability_message(availability, product_name)
                    assistant_text = f"{availability_msg}|||{_ORDER_CONFIRM_TEXT}".strip("|")

            except Exception as e:
                logger.error("Ошибка проверки наличия для '%s': %s", product_name, e, exc_info=True)
                # В случае ошибки все равно пытаемся оформить заказ
                assistant_text = f"{assistant_text}|||{_ORDER_CONFIRM_TEXT}".strip("|")
        else:
            # Если product_name не определен, все равно добавляем подтверждение
            assistant_text = f"{assistant_text}|||{_ORDER_CONFIRM_TEXT}".strip("|")

    assistant_text = _dedupe_response_parts(assistant_text)

    # 8. РС‰РµРј С„РѕС‚Рѕ С‚РѕРІР°СЂРѕРІ РёР· Google Drive
    photos = []
    user_tokens = tokenize_text(user_message)

    # Определяем режим фото: конкретный цвет → все фото этого цвета, иначе → по 1 каждого цвета
    requested_color = _detect_color_in_text(user_message)

    # Проверяем, отвечает ли клиент на вопрос о недостающих полях
    # Если да - не отправляем фото заново
    is_answering_missing_field = False
    if missing_order_fields and extracted_fields:
        # Проверяем был ли извлечен один из недостающих полей
        for field in missing_order_fields:
            if extracted_fields.get(field):
                is_answering_missing_field = True
                break

    # Primary: search photos by user message text (most reliable)
    if not is_answering_missing_field:
        try:
            found_photos = await find_product_photos(product_name=user_message)
            if found_photos:
                photos.extend(_pick_product_photos(found_photos, requested_color))
        except Exception as e:
            logger.warning(f"Failed to find photos by message text: {e}")

    if (
        not photos
        and not is_answering_missing_field
        and order_ctx.get("product")
        and (
            not target_product_type
            or _infer_product_type_from_text(order_ctx.get("product", "")) in {"", target_product_type}
        )
    ):
        try:
            found_photos = await find_product_photos(product_name=order_ctx["product"])
            if found_photos:
                photos.extend(_pick_product_photos(found_photos, requested_color))
        except Exception as e:
            logger.warning(f"Failed to find photos by order context: {e}")

    # Fallback: search by RAG metadata (товары из базы знаний)
    # Пробуем все product_name из RAG, не только те что совпали с текущим сообщением —
    # клиент мог написать "с алматы, 38", а контекст разговора про балетки
    if not photos and not is_answering_missing_field:
        for result in product_results:
            meta = result.get("metadata", {})
            photo_folder_id = meta.get("photo_folder_id", "")
            product_name = meta.get("product_name", "")

            if not (photo_folder_id or product_name):
                continue
            # Если в сообщении есть упоминание товара — приоритет; иначе всё равно пробуем (диалог уже про товар)
            if product_name and user_tokens:
                product_tokens = tokenize_text(product_name)
                if not (user_tokens & product_tokens) and len(history) <= 2:
                    continue
            try:
                folder_photos = await find_product_photos(
                    folder_id=photo_folder_id or None,
                    product_name=product_name or None,
                )
                if folder_photos:
                    photos.extend(_pick_product_photos(folder_photos, requested_color))
                    break
            except Exception as e:
                logger.warning(f"Failed to find photos for {product_name}: {e}")

    # Stage 3: search by GPT response text (модель могла назвать товар в ответе)
    if not photos and not is_answering_missing_field:
        try:
            found_photos = await find_product_photos(product_name=assistant_text)
            if found_photos:
                photos.extend(_pick_product_photos(found_photos, requested_color))
        except Exception as e:
            logger.warning(f"Failed to find photos by GPT response: {e}")

    # Stage 4: если клиент просит фото ("покажите фотку"), а товар не в текущем сообщении —
    # ищем по последнему сообщению ассистента, где был описан товар (цена, модель)
    if not photos and not is_answering_missing_field and len(history) >= 2 and _is_photo_request(user_message):
        last_product_text = None
        for m in reversed(history):
            if m.get("role") != "assistant":
                continue
            content = (m.get("content") or "").strip()
            if len(content) < 20:
                continue
            if "цена" in content.lower() or "₸" in content or "модел" in content.lower() or "chanel" in content.lower():
                last_product_text = content
                break
        if last_product_text:
            try:
                found_photos = await find_product_photos(product_name=last_product_text)
                if found_photos:
                    photos.extend(_pick_product_photos(found_photos, requested_color))
                    logger.info(f"Found {len(photos)} photos by last assistant product message")
            except Exception as e:
                logger.warning(f"Failed to find photos by last assistant message: {e}")

    if not photos and target_product_type:
        for q in _build_fallback_photo_queries(user_message, target_product_type):
            try:
                found_photos = await find_product_photos(product_name=q)
                if found_photos:
                    photos.extend(_pick_product_photos(found_photos, requested_color))
                    break
            except Exception as e:
                logger.warning(f"Failed fallback photo query '{q}': {e}")

    # Если клиент просит конкретный цвет, но этого цвета нет в фото активного товара,
    # не подменяем ответ описанием другого цвета.
    color_unavailable = False
    color_alternatives: list[str] = []
    if requested_color:
        active_product_name = order_ctx.get("product", "") or rag_product_name
        if active_product_name:
            available_colors = await _get_available_colors_for_product(active_product_name)
            if not available_colors:
                available_colors = _get_product_color_overrides(active_product_name)

            if available_colors and requested_color not in available_colors:
                assistant_text = _format_color_unavailable_message(
                    active_product_name,
                    requested_color,
                    available_colors,
                )
                photos = []
                color_unavailable = True
                if order_ctx.get("color") == requested_color:
                    order_ctx["color"] = ""
                    await upsert_order_context(chat_id, order_ctx)
            elif not photos and available_colors and requested_color in available_colors:
                # Цвет заявлен как доступный, но фото этого цвета не нашли — не подменяем другим цветом.
                assistant_text = (
                    f"По модели {active_product_name} цвет {requested_color} есть, "
                    "сейчас уточню и отправлю актуальные фото."
                )
            elif not photos:
                assistant_text = _format_color_unavailable_message(
                    active_product_name,
                    requested_color,
                    available_colors,
                )
                color_unavailable = True

    if color_unavailable:
        color_alternatives = similar_product_names or _fallback_alternative_names(
            target_product_type,
            exclude_names={order_ctx.get("product", ""), rag_product_name},
            limit=3,
        )
        assistant_text = _append_similar_products_text(assistant_text, color_alternatives)
        if not photos:
            for alt in color_alternatives:
                try:
                    found_alt = await find_product_photos(product_name=alt)
                    if not found_alt:
                        continue
                    alt_photos = _pick_product_photos(found_alt, None)
                    if target_product_type:
                        alt_photos = _filter_photos_by_requested_type(alt_photos, target_product_type)
                    if alt_photos:
                        photos = alt_photos
                        break
                except Exception as e:
                    logger.warning(f"Failed to get color-alternative photos for '{alt}': {e}")

    if target_product_type:
        photos = _filter_photos_by_requested_type(photos, target_product_type)
        if not photos:
            for q in _build_fallback_photo_queries(user_message, target_product_type):
                try:
                    found_photos = await find_product_photos(product_name=q)
                    if found_photos:
                        photos.extend(_pick_product_photos(found_photos, requested_color))
                        photos = _filter_photos_by_requested_type(photos, target_product_type)
                        if photos:
                            break
                except Exception as e:
                    logger.warning(f"Failed typed fallback photo query '{q}': {e}")

    if _is_photo_request(user_message) and not photos:
        product_label = order_ctx.get("product", "") or rag_product_name or "эту модель"
        if target_product_type == "shoes":
            assistant_text = (
                f"По запросу на туфли фото сейчас не вижу. "
                f"Могу подобрать доступные варианты по {product_label}. "
                "Показать, что есть в наличии?"
            )
        elif target_product_type == "bag":
            assistant_text = (
                f"По запросу на сумку фото сейчас не вижу. "
                f"Могу показать доступные варианты по {product_label}. "
                "Показать, что есть в наличии?"
            )
        else:
            assistant_text = (
                f"По запросу фото сейчас не вижу в каталоге для {product_label}. "
                "Могу подобрать ближайшие варианты и показать их."
            )

    model_unavailable = False
    if _is_availability_request(user_message) and specific_query_tokens:
        match_tokens = tokenize_text(primary_product_match or "")
        if not (specific_query_tokens & match_tokens):
            model_unavailable = True

    if model_unavailable:
        alternatives = _collect_similar_product_names(
            product_results,
            requested_type=target_product_type,
            exclude_names=set(),
            limit=3,
        )
        if not alternatives:
            alternatives = _collect_similar_product_names(product_results, requested_type="", exclude_names=set(), limit=3)
        if not alternatives:
            alternatives = _fallback_alternative_names(
                target_product_type,
                exclude_names={order_ctx.get("product", ""), rag_product_name},
                limit=3,
            )
        assistant_text = "Такой модели сейчас нет в наличии."
        assistant_text = _append_similar_products_text(assistant_text, alternatives)
        photos = []
        for alt in alternatives:
            try:
                found_alt = await find_product_photos(product_name=alt)
                if not found_alt:
                    continue
                alt_photos = _pick_product_photos(found_alt, None)
                if target_product_type:
                    alt_photos = _filter_photos_by_requested_type(alt_photos, target_product_type)
                if alt_photos:
                    photos = alt_photos
                    break
            except Exception as e:
                logger.warning(f"Failed to get similar product photos for '{alt}': {e}")

    photos = _dedupe_photos(photos)
    photos = _normalize_photo_captions(photos)

    assistant_text = _dedupe_response_parts(assistant_text)
    clean_text = assistant_text.replace("|||", " ").strip()
    clean_text = re.sub(r'\s{2,}', ' ', clean_text)
    await save_message(chat_id, "assistant", clean_text)

    if photos:
        logger.info(f"Found {len(photos)} photos for chat {chat_id}")

    return {
        "text": assistant_text,
        "photos": photos[:MAX_PHOTOS_PRODUCT_SHOWCASE],
        "is_new_client": is_new_client,
        "order_context": order_ctx,
        "missing_order_fields": missing_order_fields,
    }


async def handle_message(chat_id: str, sender_name: str, text: str):
    """
    РћСЃРЅРѕРІРЅРѕР№ РѕР±СЂР°Р±РѕС‚С‡РёРє СЃРѕРѕР±С‰РµРЅРёР№ (РІС‹Р·С‹РІР°РµС‚СЃСЏ РёР· webhook).
    Р“РµРЅРµСЂРёСЂСѓРµС‚ РѕС‚РІРµС‚ Рё РѕС‚РїСЂР°РІР»СЏРµС‚ С‡РµСЂРµР· Green API.
    """
    try:
        # Manager handoff commands (sent from manager's number to bot)
        if chat_id in MANAGER_CHAT_IDS:
            action, target_chat_id = _parse_handoff_command(text)
            if action:
                if not target_chat_id:
                    await send_text(
                        chat_id,
                        "Укажите номер клиента. Пример: /handoff on 77064071507",
                    )
                    return
                if action == "status":
                    enabled = await get_handoff_state(target_chat_id)
                    await send_text(
                        chat_id,
                        f"Статус для {target_chat_id}: {'ON' if enabled else 'OFF'}",
                    )
                    return
                if action == "on":
                    await set_handoff_state(target_chat_id, True)
                    await send_text(chat_id, f"Хэнд-офф включен для {target_chat_id}")
                    return
                if action == "off":
                    await set_handoff_state(target_chat_id, False)
                    await send_text(chat_id, f"Хэнд-офф выключен для {target_chat_id}")
                    return

        # If handoff enabled for this client, bot should not reply
        if await get_handoff_state(chat_id):
            logger.info(f"Handoff enabled for {chat_id}; bot skipped reply.")
            return

        result = await generate_response(chat_id, text, sender_name)

        # Split response by ||| and send as separate messages
        parts = [p.strip() for p in result["text"].split("|||") if p.strip()]

        # Determine if we should send photos
        should_send_photos = False
        if result["photos"]:
            is_photo_request = _is_photo_request(text)
            product_key = _build_product_key(tokenize_text(text), result["photos"])

            if is_photo_request:
                should_send_photos = True
            elif product_key and not await has_sent_product_photos(chat_id, product_key):
                should_send_photos = True

        if should_send_photos:
            # Send text BEFORE photos, then photos, then follow-up question AFTER photos
            follow_up = None
            # Отделяем последнюю часть как follow_up, если она содержит вопросительный знак
            if parts and "?" in parts[-1]:
                follow_up = parts[-1]
                parts = parts[:-1]

            # Отправляем текстовые части ДО фото
            for part in parts:
                await send_text(chat_id, part)
                if len(parts) > 1:
                    await asyncio.sleep(0.8)

            # Отправляем фото
            await send_multiple_images(chat_id, result["photos"])
            if product_key:
                await mark_product_photos_sent(chat_id, product_key)

            # Отправляем вопрос ПОСЛЕ фото
            if follow_up:
                await asyncio.sleep(0.8)
                await send_text(chat_id, follow_up)
        else:
            for part in parts:
                await send_text(chat_id, part)
                if len(parts) > 1:
                    await asyncio.sleep(0.8)

    except Exception as e:
        logger.error(f"Error handling message from {chat_id}: {e}", exc_info=True)
        try:
            await send_text(
                chat_id,
                "РР·РІРёРЅРёС‚Рµ, РїСЂРѕРёР·РѕС€Р»Р° РЅРµР±РѕР»СЊС€Р°СЏ РѕС€РёР±РєР°. РќР°С€ РјРµРЅРµРґР¶РµСЂ СЃРєРѕСЂРѕ СЃ РІР°РјРё СЃРІСЏР¶РµС‚СЃСЏ!",
            )
        except Exception:
            logger.error(f"Failed to send error fallback to {chat_id}", exc_info=True)

