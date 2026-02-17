"""
РњР°РїРїРёРЅРі С‚РѕРІР°СЂРѕРІ РЅР° С„РѕС‚РѕРіСЂР°С„РёРё РІ Google Drive.
РљСЌС€РёСЂСѓРµС‚ РёРЅРґРµРєСЃ РґР»СЏ Р±С‹СЃС‚СЂРѕРіРѕ РїРѕРёСЃРєР°.
"""

import json
import os
import logging

from gdrive.client import build_product_photo_index, list_images_in_folder, get_direct_download_url

logger = logging.getLogger(__name__)

CACHE_FILE = "data/photo_index.json"

# РРЅРґРµРєСЃ РІ РїР°РјСЏС‚Рё
_photo_index: dict = {}


def load_photo_index():
    """Р—Р°РіСЂСѓР·РёС‚СЊ РёРЅРґРµРєСЃ РёР· РєСЌС€Р° РёР»Рё РїРµСЂРµСЃРѕР±СЂР°С‚СЊ."""
    global _photo_index

    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                _photo_index = json.load(f)
            logger.info(f"Loaded photo index: {len(_photo_index)} products")
            return
        except Exception as e:
            logger.warning(f"Failed to load photo index cache: {e}")

    rebuild_photo_index()


def rebuild_photo_index():
    """РџРµСЂРµСЃРѕР±СЂР°С‚СЊ РёРЅРґРµРєСЃ РёР· Google Drive Рё СЃРѕС…СЂР°РЅРёС‚СЊ РІ РєСЌС€."""
    global _photo_index
    try:
        _photo_index = build_product_photo_index()
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_photo_index, f, ensure_ascii=False, indent=2)
        logger.info(f"Rebuilt photo index: {len(_photo_index)} products")
    except Exception as e:
        logger.error(f"Failed to rebuild photo index: {e}")
        _photo_index = {}


# Маппинг русских написаний брендов/моделей → английские (как в именах файлов)
_BRAND_MAP = {
    # Бренды
    "шанель": "chanel", "шанел": "chanel", "миу": "miu", "луи": "louis", "вуиттон": "vuitton",
    "луивуиттон": "vuitton", "гуччи": "gucci", "прада": "prada",
    "диор": "dior", "баленсиага": "balenciaga", "фенди": "fendi",
    "версаче": "versace", "дольче": "dolce", "габбана": "gabbana",
    "ив": "yves", "сен": "saint", "лоран": "laurent", "сенлоран": "laurent",
    "джимми": "jimmy", "джими": "jimmy", "чу": "choo", "чуу": "choo",
    "джиммичу": ["jimmy", "choo"], "джимичу": ["jimmy", "choo"],
    "джиммичо": ["jimmy", "choo"], "джимичо": ["jimmy", "choo"],
    "голден": "golden", "гус": "goose",
    "боттега": "bottega", "венета": "veneta", "селин": "celine",
    "лоэве": "loewe", "валентино": "valentino", "бурберри": "burberry",
    "эрмес": "hermes", "гермес": "hermes",
    # Модели
    "аркади": "arcadie", "аркадие": "arcadie",
    "джамбо": "jumbo", "джумбо": "jumbo", "классик": "classic", "флэп": "flap", "флап": "flap",
    "балетки": "балетки",  # уже по-русски в файлах
    "слингбэки": "slingbacks", "слингбеки": "slingbacks",
    "стар": "star", "супер": "super",
    "суперстар": ["super", "star"], "болстар": ["ball", "star"],
    "монограм": "monogram", "монограмм": "monogram",
    "почетт": "pochette", "пошет": "pochette", "фелиси": "felicie",
    "опиум": "opyum",
    "азия": "azia", "азиа": "azia",
    "саеда": "saeda",
    "беж": "беж", "черные": "черные", "розовые": "розовые",  # русские слова в файлах
    # Нормализация словоформ категорий товаров (plural/case → base form as in filenames)
    "сумки": "сумка", "сумку": "сумка", "сумок": "сумка", "сумочка": "сумка", "сумочку": "сумка",
    "кроссовок": "кроссовки", "кроссовку": "кроссовки",
    "туфель": "туфли", "туфлей": "туфли", "туфлях": "туфли",
    "балеток": "балетки", "балетку": "балетки",
}

# Связанные категории: запрос по одному слову также ищет фото по связанным
_RELATED_CATEGORIES = {
    "туфли": {"балетки"},
}

_STOP_WORDS = {
    'покажи', 'покажите', 'отправь', 'отправьте', 'скинь', 'скиньте',
    'пришли', 'пришлите', 'хочу', 'мне', 'есть', 'какие', 'фото',
    'можно', 'нужна', 'нужно', 'нужны', 'что', 'как', 'где', 'для',
    'или', 'это', 'все', 'вас', 'ваш', 'ваши', 'про', 'пожалуйста',
    'jpg', 'png', 'посмотреть', 'увидеть', 'фотку', 'фотки', 'фотографию', 'модели', 'показать',
}


def _tokenize(text: str) -> set[str]:
    """Разбить текст на значимые слова, с транслитерацией брендов."""
    import re
    words = re.findall(r'[a-zA-Zа-яА-ЯёЁ0-9]+', text.lower())
    tokens = set()
    for w in words:
        if w in _STOP_WORDS:
            continue
        # Сначала проверяем маппинг (даже для коротких слов вроде "чу")
        if w in _BRAND_MAP:
            mapped = _BRAND_MAP[w]
            if isinstance(mapped, list):
                tokens.update(mapped)
            else:
                tokens.add(mapped)
            tokens.add(w)
        elif w.isdigit() and len(w) >= 2:
            tokens.add(w)  # числа вроде "25", "95"
        elif len(w) > 2:
            tokens.add(w)
    return tokens


def tokenize_text(text: str) -> set[str]:
    """Публичный враппер для токенизации (для использования в других модулях)."""
    return _tokenize(text)


def _match_score(query_tokens: set[str], filename: str) -> int:
    """Подсчитать количество совпавших токенов запроса в имени файла."""
    file_tokens = _tokenize(filename)
    return len(query_tokens & file_tokens)


# Ключевые слова цветов в именах файлов (рус/англ) → нормализованный ключ для группировки
_COLOR_PATTERNS = [
    ("розов", "розовые"),
    ("pink", "розовые"),
    ("черн", "черные"),
    ("black", "черные"),
    ("беж", "бежевые"),
    ("beige", "бежевые"),
    ("бежев", "бежевые"),
    ("белый", "белые"),
    ("white", "белые"),
    ("красн", "красные"),
    ("red", "красные"),
    ("синий", "синие"),
    ("син", "синие"),
    ("blue", "синие"),
    ("золот", "золотые"),
    ("gold", "золотые"),
    ("серебр", "серебряные"),
    ("silver", "серебряные"),
    ("серый", "серые"),
    ("gray", "серые"),
    ("grey", "серые"),
    ("зелен", "зеленые"),
    ("green", "зеленые"),
    ("коричнев", "коричневые"),
    ("brown", "коричневые"),
    ("navy", "синие"),
]
_COLOR_ORDER = ("бежевые", "черные", "розовые", "белые", "красные", "синие", "золотые", "серебряные", "серые", "зеленые", "коричневые")


def _color_from_filename(filename: str) -> str:
    """Определить цвет по имени файла. Возвращает ключ для группировки или 'other'."""
    name = (filename or "").lower()
    for pattern, color_key in _COLOR_PATTERNS:
        if pattern in name:
            return color_key
    return "other"


def select_photos_with_color_variety(
    images: list[dict],
    max_total: int = 6,
    max_per_color: int = 2,
) -> list[dict]:
    """
    Выбрать фото с разнообразием по цвету: до max_per_color фото каждого цвета, всего до max_total.
    Подходит для балеток/сумок с вариантами розовый, черный, бежевый и т.д.
    """
    if not images or max_total <= 0:
        return []

    by_color: dict[str, list[dict]] = {}
    for img in images:
        color = _color_from_filename(img.get("filename", ""))
        by_color.setdefault(color, []).append(img)

    result = []
    # Порядок цветов: сначала бежевые, черные, розовые, остальные, в конце other
    order = [c for c in _COLOR_ORDER if c in by_color] + [c for c in sorted(by_color) if c not in _COLOR_ORDER]
    for color in order:
        if len(result) >= max_total:
            break
        take = min(max_per_color, max_total - len(result), len(by_color[color]))
        result.extend(by_color[color][:take])

    if len(result) < max_total and "other" in by_color:
        add = min(max_total - len(result), len(by_color["other"]))
        result.extend(by_color["other"][:add])
    return result[:max_total]


async def find_product_photos(
    folder_id: str | None = None,
    product_name: str | None = None,
) -> list[dict]:
    """
    Найти фото товара по folder_id (из ChromaDB metadata)
    или по нечёткому совпадению названия.
    """
    # Лениво загружаем индекс при первом обращении
    if not _photo_index:
        load_photo_index()

    # Прямой поиск по folder_id
    if folder_id:
        try:
            images = list_images_in_folder(folder_id)
            return [
                {
                    "file_id": img["id"],
                    "filename": img["name"],
                    "direct_url": get_direct_download_url(img["id"]),
                }
                for img in images
            ]
        except Exception as e:
            logger.warning(f"Failed to list images from folder {folder_id}: {e}")

    # Поиск по названию в ключах индекса (точное вхождение)
    if product_name and _photo_index:
        name_lower = product_name.lower()
        for key, value in _photo_index.items():
            if key == "root":
                continue  # пропускаем root, ищем по конкретным папкам
            if name_lower in key or key in name_lower:
                logger.info(f"Photo match by folder key '{key}' for '{product_name}'")
                return value["images"]

    # Токенизированный поиск по именам файлов
    if product_name and _photo_index:
        query_tokens = _tokenize(product_name)
        if not query_tokens:
            logger.debug(f"No significant tokens in '{product_name}'")
            return []

        # Расширяем токены связанными категориями (туфли → +балетки)
        extra_tokens: set[str] = set()
        for token in query_tokens:
            if token in _RELATED_CATEGORIES:
                extra_tokens.update(_RELATED_CATEGORIES[token])
        expanded_tokens = query_tokens | extra_tokens

        # Считаем кол-во значимых слов в исходном запросе (до маппинга)
        import re as _re
        _orig_words = _re.findall(r'[a-zA-Zа-яА-ЯёЁ0-9]+', product_name.lower())
        _meaningful = [w for w in _orig_words if w not in _STOP_WORDS and (len(w) > 2 or w in _BRAND_MAP)]
        min_score = 2 if len(_meaningful) >= 2 else 1

        scored_images = []
        for value in _photo_index.values():
            for img in value.get("images", []):
                score = _match_score(expanded_tokens, img.get("filename", ""))
                if score >= min_score:
                    scored_images.append((score, img))

        if scored_images:
            # Сортируем по количеству совпадений (больше = лучше)
            scored_images.sort(key=lambda x: x[0], reverse=True)
            best_score = scored_images[0][0]
            # Берём только фото с лучшим score
            best_matches = [img for score, img in scored_images if score == best_score]
            logger.info(
                f"Photo match by tokens {query_tokens} (expanded: {expanded_tokens}): "
                f"{len(best_matches)} photos, best score={best_score}, min_score={min_score}"
            )
            return best_matches

    logger.info(f"No photos found for folder_id={folder_id}, product_name={product_name}")
    return []
