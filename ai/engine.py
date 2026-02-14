"""
AI-оркестратор: связывает RAG, GPT и Google Drive.
Главная точка обработки сообщений.
"""

import asyncio
import json
import logging
import re
import time

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
from notifications import notify_error
from integrations.n8n import notify_order_confirmed
from ai.order_manager import (
    _normalize_product_type,
    _infer_product_type_from_text,
    _merge_order_context,
    _contains_order_confirm,
    _strip_order_confirm,
    _build_missing_fields,
    _question_for_missing,
    _has_question,
    _has_order_intent,
    _assistant_already_requests_missing,
    _strip_checkout_prompts,
    _get_product_color_overrides,
    _ORDER_CONFIRM_TEXT,
)
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

# Слова категорий товаров — если клиент упоминает категорию, это новый запрос, а не follow-up
_CATEGORY_WORDS = {
    "сумка", "сумки", "сумку", "сумок", "сумочка", "сумочку",
    "кроссовки", "кроссовок", "кроссовку",
    "туфли", "туфель", "туфлей", "туфлях",
    "балетки", "балеток", "балетку",
    "обувь", "обуви",
}


def _is_category_browsing(user_message: str) -> bool:
    """Клиент просматривает категорию ('какие сумки есть', 'покажите кроссовки').
    НЕ считается категорией, если упомянут конкретный бренд/модель.
    """
    words = re.findall(r'[а-яА-ЯёЁa-zA-Z]+', user_message.lower())
    has_category = any(w in _CATEGORY_WORDS for w in words)
    if not has_category:
        return False
    # Если есть конкретный бренд/модель — это запрос конкретного товара, не категория
    has_product_hint = any(w in _PRODUCT_HINT_TOKENS for w in words)
    if has_product_hint:
        return False
    return True


def _detect_browsing_category(user_message: str) -> str:
    """Определить тип товара из категориального запроса. Возвращает product_type или ''."""
    text_l = user_message.lower()
    if any(w in text_l for w in ("сумк", "сумоч")):
        return "bag"
    if any(w in text_l for w in ("кроссовк", )):
        return "shoes"
    if any(w in text_l for w in ("туфл", )):
        return "shoes"
    if any(w in text_l for w in ("балетк", )):
        return "shoes"
    if any(w in text_l for w in ("обувь", "обуви")):
        return "shoes"
    return ""


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
    # Клиент упоминает категорию товара ("сумки", "кроссовки", "туфли") — это новый запрос
    if any(w in _CATEGORY_WORDS for w in re.findall(r'[а-яА-ЯёЁa-zA-Z]+', text_l)):
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

_COLOR_REQUIREMENT_CACHE: dict[str, tuple[bool, float]] = {}
_COLOR_CACHE_TTL = 1800  # 30 minutes
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


_BRAND_NAMES_EN = {
    "chanel", "saint laurent", "ysl", "yves saint laurent",
    "jimmy choo", "miu miu", "louis vuitton",
    "gucci", "dior", "golden goose", "prada",
    "balenciaga", "fendi", "versace", "dolce gabbana",
    "bottega veneta", "celine", "loewe", "valentino",
    "burberry", "hermes",
}

_BRAND_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(b) for b in sorted(_BRAND_NAMES_EN, key=len, reverse=True)) + r")\b\s*([\w\-]+(?:\s+[\w\-]+){0,2})?",
    re.IGNORECASE,
)


def _extract_product_mention(text: str) -> str:
    """Extract first brand + model mention from text. Returns short product name or empty string."""
    if not text:
        return ""
    m = _BRAND_PATTERN.search(text)
    if not m:
        return ""
    brand = m.group(1).strip()
    rest = (m.group(2) or "").strip()
    if rest:
        return f"{brand} {rest}"
    return brand


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

    # Если клиент еще не выбрал товар - явно запретить собирать город
    product_warning = ""
    if not order_ctx.get("product"):
        product_warning = "\n⚠️ ВАЖНО: Клиент еще НЕ ВЫБРАЛ товар. НЕ спрашивай город! Помоги с выбором, ответь на вопросы.\n"

    # Для сумок размер не нужен
    bag_note = ""
    if order_ctx.get("product_type") == "bag":
        bag_note = "\n⚠️ Тип товара — сумка. У сумок НЕТ размера, НЕ спрашивай размер!\n"

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
        + product_warning
        + bag_note +
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
            "city": str(parsed.get("city") or ""),
            "product": str(parsed.get("product") or ""),
            "product_type": str(parsed.get("product_type") or ""),
            "size": str(parsed.get("size") or ""),
            "color": str(parsed.get("color") or ""),
            "address": str(parsed.get("address") or ""),
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
    cached = _COLOR_REQUIREMENT_CACHE.get(product)
    if cached is not None:
        value, ts = cached
        if time.time() - ts < _COLOR_CACHE_TTL:
            return value
    try:
        photos = await find_product_photos(product_name=product_name)
        colors = {_detect_color_from_filename(p.get("filename", "")) for p in photos}
        colors.discard("")
        required = len(colors) > 1
        _COLOR_REQUIREMENT_CACHE[product] = (required, time.time())
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


def _product_key_from_filename(filename: str) -> str:
    """Извлечь ключ товара из имени файла для группировки.
    'Сумка черная Chanel 25 2.jpg' → 'сумка черная chanel 25'
    'сумка Miu Miu Arcadie 1.jpg' → 'сумка miu miu arcadie'
    """
    name = filename.lower()
    name = re.sub(r'\.\w+$', '', name)  # убрать расширение
    name = re.sub(r'\s+\d+$', '', name)  # убрать порядковый номер фото
    return name.strip()


def _pick_product_photos(found_photos: list[dict], requested_color: str | None = None) -> list[dict]:
    """
    Выбрать фото товара.
    - requested_color задан → отфильтровать по цвету, отдать все подходящие
    - requested_color = None → обзорный режим:
      - если несколько разных товаров → по 1 фото каждого товара (витрина)
      - если один товар → по 1 фото каждого цвета
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
        # Группируем по товару (без номера фото и расширения)
        product_groups: dict[str, list[dict]] = {}
        for p in found_photos:
            key = _product_key_from_filename(p.get("filename", ""))
            product_groups.setdefault(key, []).append(p)

        if len(product_groups) > 1:
            # Витрина: несколько разных товаров → по 1 фото каждого
            picked = []
            for key in product_groups:
                picked.append(product_groups[key][0])
                if len(picked) >= MAX_PHOTOS_PRODUCT_SHOWCASE:
                    break
            picked = _dedupe_photos(picked)
        else:
            # Один товар — по 1 фото каждого цвета
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
    Генерирует ответ бота.
    Возвращает: {'text': str, 'photos': list[dict]}
    """
    # 1. Сохраняем входящее сообщение
    await save_message(chat_id, "user", user_message, sender_name)

    # Сбрасываем дожим когда клиент отвечает
    await reset_nudge_state(chat_id)

    # Предварительно читаем контекст заказа, чтобы не терять активный товар
    current_order_ctx = await get_order_context(chat_id)
    requested_product_type = _infer_product_type_from_text(user_message)

    # Определяем, просматривает ли клиент категорию ("какие сумки есть?")
    browsing_category = _is_category_browsing(user_message)
    browsing_type = _detect_browsing_category(user_message) if browsing_category else ""

    # Если клиент переключился на ДРУГУЮ категорию — сбросить контекст заказа
    if browsing_category and current_order_ctx.get("product"):
        old_type = current_order_ctx.get("product_type", "")
        if browsing_type and old_type and browsing_type != old_type:
            logger.info(f"[{chat_id}] Category switch: {old_type} -> {browsing_type}, resetting order context")
            current_order_ctx = {"product_type": browsing_type}
            await upsert_order_context(chat_id, current_order_ctx)
        elif browsing_category:
            # Та же или неизвестная категория, но клиент спрашивает "какие есть" — сброс товара
            logger.info(f"[{chat_id}] Category browsing detected, clearing product from order context")
            current_order_ctx["product"] = ""
            current_order_ctx["size"] = ""
            current_order_ctx["color"] = ""
            current_order_ctx["address"] = ""
            if browsing_type:
                current_order_ctx["product_type"] = browsing_type
            await upsert_order_context(chat_id, current_order_ctx)

    product_query = user_message
    if _should_use_active_product_query(user_message, current_order_ctx.get("product", "")):
        product_query = current_order_ctx.get("product", "") or user_message

    # 2. Параллельно ищем в базе знаний
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

    # 3. Собираем контексты
    product_context = "\n---\n".join([r["text"] for r in product_results])
    product_context = product_context or "Нет релевантных товаров в базе."

    sales_context = "\n---\n".join([r["text"] for r in script_results])
    sales_context = sales_context or "Нет релевантных скриптов."

    # 4. История переписки
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

    # При browse категории НЕ назначаем RAG продукт в заказ (клиент ещё не выбрал)
    if rag_product_name and not extracted_fields.get("product") and not order_ctx.get("product") and not browsing_category:
        extracted_fields["product"] = rag_product_name
    if not extracted_fields.get("product_type"):
        extracted_fields["product_type"] = _infer_product_type_from_text(
            extracted_fields.get("product") or rag_product_name
        )
    if target_product_type:
        extracted_fields["product_type"] = target_product_type

    # Before merge — capture what WAS missing (for is_answering_missing_field check later)
    color_required_pre = await _is_color_required(order_ctx.get("product", ""))
    pre_merge_missing = _build_missing_fields(order_ctx, color_required_pre)

    order_ctx = _merge_order_context(order_ctx, extracted_fields)
    if not order_ctx.get("product") and rag_product_name and not browsing_category:
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

    # 6. Вызываем GPT
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
            and not browsing_category  # НЕ задавать вопросы при просмотре категории
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
                        "[%s] Товар '%s' (size=%s, color=%s) нет в наличии",
                        chat_id, product_name, size, color
                    )
                    assistant_text = format_availability_message(availability, product_name)

                    # Предлагаем альтернативы если есть
                    if similar_product_names:
                        alternatives_text = "Могу предложить похожие варианты: " + ", ".join(similar_product_names)
                        assistant_text = f"{assistant_text}|||{alternatives_text}".strip("|")

                else:
                    # Товар в наличии - можно оформлять заказ
                    logger.info(
                        "[%s] Товар '%s' (size=%s, color=%s) в наличии: quantity=%d, price=%s",
                        chat_id, product_name, size, color,
                        availability["quantity"], availability["price"]
                    )
                    availability_msg = format_availability_message(availability, product_name)
                    assistant_text = f"{availability_msg}|||{_ORDER_CONFIRM_TEXT}".strip("|")
                    # Уведомляем N8N о подтверждении заказа
                    asyncio.create_task(notify_order_confirmed(chat_id, order_ctx, sender_name))

            except Exception as e:
                logger.error("[%s] Ошибка проверки наличия для '%s': %s", chat_id, product_name, e, exc_info=True)
                # В случае ошибки все равно пытаемся оформить заказ
                assistant_text = f"{assistant_text}|||{_ORDER_CONFIRM_TEXT}".strip("|")
        else:
            # Если product_name не определен, все равно добавляем подтверждение
            assistant_text = f"{assistant_text}|||{_ORDER_CONFIRM_TEXT}".strip("|")

    assistant_text = _dedupe_response_parts(assistant_text)

    # 8. Ищем фото товаров из Google Drive
    photos = []
    user_tokens = tokenize_text(user_message)

    # Определяем режим фото: конкретный цвет → все фото этого цвета, иначе → по 1 каждого цвета
    requested_color = _detect_color_in_text(user_message)

    # Проверяем, отвечает ли клиент на вопрос о недостающих полях
    # Если да - не отправляем фото заново
    # Используем pre_merge_missing (до слияния), т.к. после merge поле уже не "missing"
    is_answering_missing_field = False
    if pre_merge_missing and extracted_fields:
        for field in pre_merge_missing:
            if extracted_fields.get(field):
                is_answering_missing_field = True
                break

    # Primary: search photos by user message text (most reliable)
    # ВАЖНО: Ищем фото ВСЕГДА, даже если клиент отвечает на вопрос про товар
    # (он может спрашивать "какие есть кроссовки?", а не отвечать на вопрос)
    try:
        found_photos = await find_product_photos(product_name=user_message)
        if found_photos:
            photos.extend(_pick_product_photos(found_photos, requested_color))
    except Exception as e:
        logger.warning(f"[{chat_id}] Failed to find photos by message text: {e}")

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
            logger.warning(f"[{chat_id}] Failed to find photos by order context: {e}")

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
                logger.warning(f"[{chat_id}] Failed to find photos for {product_name}: {e}")

    # Stage 3: search by product mention in GPT response (not full text)
    if not photos and not is_answering_missing_field:
        gpt_product = _extract_product_mention(assistant_text)
        if gpt_product:
            try:
                found_photos = await find_product_photos(product_name=gpt_product)
                if found_photos:
                    photos.extend(_pick_product_photos(found_photos, requested_color))
            except Exception as e:
                logger.warning(f"[{chat_id}] Failed to find photos by GPT response product '{gpt_product}': {e}")

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
                    logger.info(f"[{chat_id}] Found {len(photos)} photos by last assistant product message")
            except Exception as e:
                logger.warning(f"[{chat_id}] Failed to find photos by last assistant message: {e}")

    if not photos and target_product_type:
        for q in _build_fallback_photo_queries(user_message, target_product_type):
            try:
                found_photos = await find_product_photos(product_name=q)
                if found_photos:
                    photos.extend(_pick_product_photos(found_photos, requested_color))
                    break
            except Exception as e:
                logger.warning(f"[{chat_id}] Failed fallback photo query '{q}': {e}")

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
                    logger.warning(f"[{chat_id}] Failed to get color-alternative photos for '{alt}': {e}")

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
                    logger.warning(f"[{chat_id}] Failed typed fallback photo query '{q}': {e}")

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
                logger.warning(f"[{chat_id}] Failed to get similar product photos for '{alt}': {e}")

    photos = _dedupe_photos(photos)
    photos = _normalize_photo_captions(photos)

    # После показа фото категории — добавить вопрос "Какую выбираете?"
    if browsing_category and photos and "?" not in assistant_text:
        assistant_text = f"{assistant_text}|||Какую модель хотите рассмотреть поближе? 😊"

    assistant_text = _dedupe_response_parts(assistant_text)
    clean_text = assistant_text.replace("|||", " ").strip()
    clean_text = re.sub(r'\s{2,}', ' ', clean_text)
    await save_message(chat_id, "assistant", clean_text)

    if photos:
        logger.info(f"[{chat_id}] Found {len(photos)} photos")

    return {
        "text": assistant_text,
        "photos": photos[:MAX_PHOTOS_PRODUCT_SHOWCASE],
        "is_new_client": is_new_client,
        "order_context": order_ctx,
        "missing_order_fields": missing_order_fields,
    }


async def handle_message(chat_id: str, sender_name: str, text: str):
    """
    Основной обработчик сообщений (вызывается из webhook).
    Генерирует ответ и отправляет через Green API.
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

        # If handoff enabled for this client, save message but don't reply
        if await get_handoff_state(chat_id):
            await save_message(chat_id, "user", text, sender_name)
            await update_last_client_message(chat_id, text)
            logger.info(f"[{chat_id}] Handoff enabled; saved message, bot skipped reply.")
            return

        result = await generate_response(chat_id, text, sender_name)

        # Split response by ||| and send as separate messages
        parts = [p.strip() for p in result["text"].split("|||") if p.strip()]

        # Determine if we should send photos
        should_send_photos = False
        if result["photos"]:
            is_photo_request = _is_photo_request(text)
            product_key = _build_product_key(tokenize_text(text), result["photos"])

            if is_photo_request or _is_category_browsing(text):
                # Клиент просит показать/посмотреть — отправляем даже если уже отправляли
                should_send_photos = True
            elif product_key and not await has_sent_product_photos(chat_id, product_key):
                # Новый товар, фото ещё не отправляли
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

            # Добавляем подписи к фото (название модели) для контекста reply
            photos_with_captions = []
            photo_names = []
            for img in result["photos"]:
                fname = img.get("filename", "")
                caption = _product_key_from_filename(fname) if fname else ""
                # Красивая подпись: убираем лишние пробелы, capitalize
                caption = re.sub(r'\s+', ' ', caption).strip()
                if caption:
                    caption = caption[0].upper() + caption[1:]
                photo_names.append(caption or fname)
                photos_with_captions.append({**img, "caption": caption})

            # Отправляем фото
            await send_multiple_images(chat_id, photos_with_captions)
            if product_key:
                await mark_product_photos_sent(chat_id, product_key)

            # Сохраняем список показанных фото в историю, чтобы GPT знал контекст
            unique_names = list(dict.fromkeys(photo_names))  # сохранить порядок, убрать дубли
            if unique_names:
                photo_note = "[Показаны фото: " + ", ".join(unique_names) + "]"
                await save_message(chat_id, "assistant", photo_note, "")

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
        logger.error(f"[{chat_id}] Error handling message: {e}", exc_info=True)
        await notify_error("handle_message", f"chat_id={chat_id} error={e}")
        try:
            await send_text(
                chat_id,
                "Извините, произошла небольшая ошибка. Наш менеджер скоро с вами свяжется!",
            )
        except Exception:
            logger.error(f"[{chat_id}] Failed to send error fallback", exc_info=True)

