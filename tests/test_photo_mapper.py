"""
Tests for gdrive/photo_mapper.py — tokenization, photo search, color/product grouping.

This is the most fragile module: no stemming, exact token matching,
brand/word-form mappings. Every fix here must be covered by a test.
"""

import pytest
from unittest.mock import patch

from gdrive.photo_mapper import (
    _tokenize,
    tokenize_text,
    _match_score,
    _color_from_filename,
    select_photos_with_color_variety,
    find_product_photos,
)

# ── Реальные данные из photo_index.json ──────────────────────────────────────

PHOTO_INDEX_DATA = {
    "root": {
        "folder_id": "root_folder_id",
        "path": "",
        "images": [
            {"file_id": "miumiu2", "filename": "сумка Miu Miu Arcadie 2.jpg", "direct_url": ""},
            {"file_id": "miumiu1", "filename": "сумка Miu Miu Arcadie 1.jpg", "direct_url": ""},
            {"file_id": "lv1", "filename": "сумка Louis Vuitton Pochette Félicie.jpg", "direct_url": ""},
            {"file_id": "opyum2", "filename": "туфли черные Saint Laurent Opyum 2.jpg", "direct_url": ""},
            {"file_id": "opyum1", "filename": "туфли черные Saint Laurent Opyum 1.jpg", "direct_url": ""},
            {"file_id": "saeda2", "filename": "туфли черные Jimmy Choo Saeda 2.jpg", "direct_url": ""},
            {"file_id": "saeda1", "filename": "туфли черные Jimmy Choo Saeda 1.jpg", "direct_url": ""},
            {"file_id": "azia2", "filename": "туфли черные Jimmy Choo Azia 95 2.jpg", "direct_url": ""},
            {"file_id": "azia1", "filename": "туфли черные Jimmy Choo Azia 95 1.jpg", "direct_url": ""},
            {"file_id": "gg_pink", "filename": "кроссовки розовые Golden Goose Star (Custom Co-Creation).jpg", "direct_url": ""},
            {"file_id": "gg_ball2", "filename": "кроссовки черные Golden Goose Ball Star 2.jpg", "direct_url": ""},
            {"file_id": "gg_ball1", "filename": "кроссовки черные Golden Goose Ball Star 1.jpg", "direct_url": ""},
            {"file_id": "gg_white", "filename": "кроссовки белые Golden Goose Super-Star.jpg", "direct_url": ""},
            {"file_id": "gg_custom", "filename": "кроссовки Golden Goose Super-Star Custom.jpg", "direct_url": ""},
            {"file_id": "sling3", "filename": "туфли Chanel Classic Slingbacks 3.jpg", "direct_url": ""},
            {"file_id": "sling2", "filename": "туфли Chanel Classic Slingbacks 2.jpg", "direct_url": ""},
            {"file_id": "sling1", "filename": "туфли Chanel Classic Slingbacks 1.jpg", "direct_url": ""},
            {"file_id": "ysl1", "filename": "Сумка черная Yves Saint Laurent Monogram.jpg", "direct_url": ""},
            {"file_id": "chanel_jumbo2", "filename": "Сумка черная Chanel Jumbo Classic Flap 2.jpg", "direct_url": ""},
            {"file_id": "chanel_jumbo1", "filename": "Сумка черная Chanel Jumbo Classic Flap 1.jpg", "direct_url": ""},
            {"file_id": "chanel25_3", "filename": "Сумка черная Chanel 25 3.jpg", "direct_url": ""},
            {"file_id": "chanel25_2", "filename": "Сумка черная Chanel 25 2.jpg", "direct_url": ""},
            {"file_id": "chanel25_1", "filename": "Сумка черная Chanel 25 1.jpg", "direct_url": ""},
            {"file_id": "bal_beige2", "filename": "Chanel балетки бежевые 2.jpg", "direct_url": ""},
            {"file_id": "bal_beige1", "filename": "Chanel балетки бежевые 1.jpg", "direct_url": ""},
            {"file_id": "bal_pink2", "filename": "Chanel балетки розовые 2.jpg", "direct_url": ""},
            {"file_id": "bal_pink1", "filename": "Chanel балетки розовые 1.jpg", "direct_url": ""},
            {"file_id": "bal_black2", "filename": "Chanel балетки черные 2.jpg", "direct_url": ""},
            {"file_id": "bal_black1", "filename": "Chanel балетки черные 1.jpg", "direct_url": ""},
        ],
    }
}


@pytest.fixture(autouse=True)
def mock_photo_index():
    """Подставляем тестовый индекс вместо реального."""
    import gdrive.photo_mapper as pm
    original = pm._photo_index.copy()
    pm._photo_index.clear()
    pm._photo_index.update(PHOTO_INDEX_DATA)
    yield
    pm._photo_index.clear()
    pm._photo_index.update(original)


# ── A) Tokenization ─────────────────────────────────────────────────────────


class TestTokenize:
    """Тесты _tokenize: стоп-слова, бренд-маппинг, нормализация словоформ."""

    def test_category_sumki(self):
        """'какие сумки у вас есть' → содержит 'сумка' (нормализация plural→singular)."""
        tokens = _tokenize("какие сумки у вас есть")
        assert "сумка" in tokens, f"Expected 'сумка' in tokens, got {tokens}"

    def test_category_krossovki(self):
        """'покажите кроссовки Golden Goose' → кроссовки + бренд."""
        tokens = _tokenize("покажите кроссовки Golden Goose")
        assert "кроссовки" in tokens
        assert "golden" in tokens
        assert "goose" in tokens

    def test_brand_chanel_25(self):
        """'Chanel 25 черная' → chanel, 25, черная."""
        tokens = _tokenize("Chanel 25 черная")
        assert "chanel" in tokens
        assert "25" in tokens

    def test_brand_jimmy_choo_russian(self):
        """'джимми чу' → jimmy, choo (бренд-маппинг)."""
        tokens = _tokenize("джимми чу")
        assert "jimmy" in tokens
        assert "choo" in tokens

    def test_category_baletki(self):
        """'балетки' → 'балетки'."""
        tokens = _tokenize("балетки")
        assert "балетки" in tokens

    def test_category_tufli(self):
        """'туфли' → 'туфли'."""
        tokens = _tokenize("туфли")
        assert "туфли" in tokens

    def test_word_form_sumku(self):
        """'хочу сумку' → содержит 'сумка' (нормализация 'сумку'→'сумка')."""
        tokens = _tokenize("хочу сумку")
        assert "сумка" in tokens

    def test_word_form_sumochka(self):
        """'сумочка' → содержит 'сумка'."""
        tokens = _tokenize("покажите сумочку")
        assert "сумка" in tokens

    def test_word_form_krossovok(self):
        """'кроссовок' → 'кроссовки'."""
        tokens = _tokenize("кроссовок")
        assert "кроссовки" in tokens

    def test_stop_words_removed(self):
        """Стоп-слова ('какие', 'фото', 'покажите') удаляются."""
        tokens = _tokenize("какие фото покажите")
        assert "какие" not in tokens
        assert "фото" not in tokens
        assert "покажите" not in tokens

    def test_short_words_filtered(self):
        """Слова из 1-2 символов отфильтровываются (кроме маппинга)."""
        tokens = _tokenize("я у к")
        assert len(tokens) == 0

    def test_numbers_kept(self):
        """Числа >= 2 цифр сохраняются (размеры, модели)."""
        tokens = _tokenize("размер 38")
        assert "38" in tokens

    def test_brand_miu_miu(self):
        """'миу миу' → 'miu'."""
        tokens = _tokenize("миу миу")
        assert "miu" in tokens


# ── B) Match Score ───────────────────────────────────────────────────────────


class TestMatchScore:
    """Тесты _match_score: подсчёт совпавших токенов."""

    def test_exact_match(self):
        """'chanel 25' vs filename 'Сумка черная Chanel 25 1.jpg' → score >= 2."""
        score = _match_score({"chanel", "25"}, "Сумка черная Chanel 25 1.jpg")
        assert score >= 2

    def test_partial_match(self):
        """'сумка' vs filename 'сумка Miu Miu Arcadie 1.jpg' → score >= 1."""
        score = _match_score({"сумка"}, "сумка Miu Miu Arcadie 1.jpg")
        assert score >= 1

    def test_no_match(self):
        """'платье' vs filename with bags → score 0."""
        score = _match_score({"платье"}, "сумка Miu Miu Arcadie 1.jpg")
        assert score == 0


# ── C) Color Detection ──────────────────────────────────────────────────────


class TestColorFromFilename:
    """Тесты определения цвета из имени файла."""

    def test_black(self):
        assert _color_from_filename("Сумка черная Chanel 25 1.jpg") == "черные"

    def test_beige(self):
        assert _color_from_filename("Chanel балетки бежевые 1.jpg") == "бежевые"

    def test_pink(self):
        assert _color_from_filename("Chanel балетки розовые 1.jpg") == "розовые"

    def test_white(self):
        # NB: "Golden" содержит "gold" → триггерит "золотые". Известная особенность.
        # Тестируем файл без бренда Golden:
        assert _color_from_filename("кроссовки белый цвет.jpg") == "белые"

    def test_no_color(self):
        assert _color_from_filename("сумка Miu Miu Arcadie 1.jpg") == "other"


# ── D) Color Variety Selection ───────────────────────────────────────────────


class TestColorVariety:
    """select_photos_with_color_variety: по 1 фото каждого цвета."""

    def test_baletki_3_colors(self):
        """Балетки 3 цветов → 3 фото (по 1 каждого цвета)."""
        images = [
            {"file_id": "bal_beige1", "filename": "Chanel балетки бежевые 1.jpg"},
            {"file_id": "bal_beige2", "filename": "Chanel балетки бежевые 2.jpg"},
            {"file_id": "bal_pink1", "filename": "Chanel балетки розовые 1.jpg"},
            {"file_id": "bal_pink2", "filename": "Chanel балетки розовые 2.jpg"},
            {"file_id": "bal_black1", "filename": "Chanel балетки черные 1.jpg"},
            {"file_id": "bal_black2", "filename": "Chanel балетки черные 2.jpg"},
        ]
        picked = select_photos_with_color_variety(images, max_total=6, max_per_color=1)
        colors = {_color_from_filename(p["filename"]) for p in picked}
        assert colors == {"бежевые", "розовые", "черные"}
        assert len(picked) == 3

    def test_max_total_limit(self):
        """max_total ограничивает общее кол-во."""
        images = [
            {"file_id": f"img{i}", "filename": f"товар цвет{i} {i}.jpg"}
            for i in range(20)
        ]
        picked = select_photos_with_color_variety(images, max_total=4, max_per_color=2)
        assert len(picked) <= 4


# ── E) Photo Search (find_product_photos) ───────────────────────────────────


class TestFindProductPhotos:
    """find_product_photos: поиск фото по названию товара."""

    @pytest.mark.asyncio
    async def test_sumki_finds_all_bags(self):
        """'сумки' → находит ВСЕ 5 видов сумок (9 фото)."""
        photos = await find_product_photos(product_name="сумки")
        filenames = [p["filename"] for p in photos]
        # Должны быть все 5 видов сумок
        assert any("Miu Miu" in f for f in filenames), "Missing Miu Miu bag"
        assert any("Louis Vuitton" in f for f in filenames), "Missing LV bag"
        assert any("Yves Saint Laurent" in f for f in filenames), "Missing YSL bag"
        assert any("Chanel Jumbo" in f for f in filenames), "Missing Chanel Jumbo bag"
        assert any("Chanel 25" in f for f in filenames), "Missing Chanel 25 bag"

    @pytest.mark.asyncio
    async def test_chanel_25_specific(self):
        """'Chanel 25' → только Chanel 25 (score 2 > score 1 для других)."""
        photos = await find_product_photos(product_name="Chanel 25")
        filenames = [p["filename"] for p in photos]
        assert len(photos) >= 1
        assert all("Chanel 25" in f or "chanel" in f.lower() for f in filenames)

    @pytest.mark.asyncio
    async def test_baletki_finds_all(self):
        """'балетки' → все балетки (6 фото)."""
        photos = await find_product_photos(product_name="балетки")
        filenames = [p["filename"] for p in photos]
        assert len(photos) == 6
        assert all("балетки" in f.lower() for f in filenames)

    @pytest.mark.asyncio
    async def test_krossovki_finds_all(self):
        """'кроссовки' → все кроссовки."""
        photos = await find_product_photos(product_name="кроссовки")
        filenames = [p["filename"] for p in photos]
        assert len(photos) >= 4
        assert all("кроссовки" in f.lower() for f in filenames)

    @pytest.mark.asyncio
    async def test_jimmy_choo_azia(self):
        """'Jimmy Choo Azia' → только Azia (не Saeda)."""
        photos = await find_product_photos(product_name="Jimmy Choo Azia")
        filenames = [p["filename"] for p in photos]
        assert len(photos) >= 1
        assert all("Azia" in f for f in filenames)

    @pytest.mark.asyncio
    async def test_no_match_returns_empty(self):
        """Несуществующий товар → пустой список."""
        photos = await find_product_photos(product_name="платье Zara")
        assert photos == []

    @pytest.mark.asyncio
    async def test_russian_brand_mapping(self):
        """'джимми чу азия' → находит Jimmy Choo Azia."""
        photos = await find_product_photos(product_name="джимми чу азия")
        filenames = [p["filename"] for p in photos]
        assert len(photos) >= 1
        assert any("Azia" in f for f in filenames)


# ── F) Product Key Grouping (from engine.py) ────────────────────────────────


class TestProductKeyFromFilename:
    """_product_key_from_filename: группировка по товару (без номера фото)."""

    def test_import_and_basic(self):
        from ai.engine import _product_key_from_filename
        assert _product_key_from_filename("Сумка черная Chanel 25 2.jpg") == "сумка черная chanel 25"
        assert _product_key_from_filename("сумка Miu Miu Arcadie 1.jpg") == "сумка miu miu arcadie"

    def test_same_product_same_key(self):
        """Разные фото одного товара → одинаковый ключ."""
        from ai.engine import _product_key_from_filename
        key1 = _product_key_from_filename("Сумка черная Chanel 25 1.jpg")
        key2 = _product_key_from_filename("Сумка черная Chanel 25 2.jpg")
        key3 = _product_key_from_filename("Сумка черная Chanel 25 3.jpg")
        assert key1 == key2 == key3

    def test_different_products_different_keys(self):
        """Разные товары → разные ключи."""
        from ai.engine import _product_key_from_filename
        key_miu = _product_key_from_filename("сумка Miu Miu Arcadie 1.jpg")
        key_ysl = _product_key_from_filename("Сумка черная Yves Saint Laurent Monogram.jpg")
        key_chanel = _product_key_from_filename("Сумка черная Chanel 25 1.jpg")
        assert key_miu != key_ysl
        assert key_miu != key_chanel
        assert key_ysl != key_chanel


# ── G) Pick Product Photos — витрина vs один товар ──────────────────────────


class TestPickProductPhotos:
    """_pick_product_photos: витрина (по 1 каждого товара) vs один товар (по цветам)."""

    def test_showcase_multiple_products(self):
        """5 разных сумок → 5 фото (по 1 каждого товара)."""
        from ai.engine import _pick_product_photos
        all_bags = [
            {"file_id": "miumiu1", "filename": "сумка Miu Miu Arcadie 1.jpg"},
            {"file_id": "miumiu2", "filename": "сумка Miu Miu Arcadie 2.jpg"},
            {"file_id": "lv1", "filename": "сумка Louis Vuitton Pochette Félicie.jpg"},
            {"file_id": "ysl1", "filename": "Сумка черная Yves Saint Laurent Monogram.jpg"},
            {"file_id": "chanel_jumbo1", "filename": "Сумка черная Chanel Jumbo Classic Flap 1.jpg"},
            {"file_id": "chanel_jumbo2", "filename": "Сумка черная Chanel Jumbo Classic Flap 2.jpg"},
            {"file_id": "chanel25_1", "filename": "Сумка черная Chanel 25 1.jpg"},
            {"file_id": "chanel25_2", "filename": "Сумка черная Chanel 25 2.jpg"},
            {"file_id": "chanel25_3", "filename": "Сумка черная Chanel 25 3.jpg"},
        ]
        picked = _pick_product_photos(all_bags)

        # Должны быть все 5 разных товаров
        filenames = [p["filename"] for p in picked]
        assert len(picked) == 5, f"Expected 5 photos (1 per product), got {len(picked)}: {filenames}"

        # Проверяем что каждый товар представлен
        assert any("Miu Miu" in f for f in filenames), "Missing Miu Miu"
        assert any("Louis Vuitton" in f for f in filenames), "Missing LV"
        assert any("Yves Saint Laurent" in f for f in filenames), "Missing YSL"
        assert any("Chanel Jumbo" in f for f in filenames), "Missing Chanel Jumbo"
        assert any("Chanel 25" in f for f in filenames), "Missing Chanel 25"

    def test_single_product_color_variety(self):
        """Балетки одной модели в 3 цветах → 3 фото (по 1 цвета)."""
        from ai.engine import _pick_product_photos
        baletki = [
            {"file_id": "bal_beige1", "filename": "Chanel балетки бежевые 1.jpg"},
            {"file_id": "bal_beige2", "filename": "Chanel балетки бежевые 2.jpg"},
            {"file_id": "bal_pink1", "filename": "Chanel балетки розовые 1.jpg"},
            {"file_id": "bal_pink2", "filename": "Chanel балетки розовые 2.jpg"},
            {"file_id": "bal_black1", "filename": "Chanel балетки черные 1.jpg"},
            {"file_id": "bal_black2", "filename": "Chanel балетки черные 2.jpg"},
        ]
        picked = _pick_product_photos(baletki)
        assert len(picked) == 3, f"Expected 3 photos (1 per color), got {len(picked)}"

    def test_color_filter(self):
        """Запрос 'черная сумка' → только черные сумки."""
        from ai.engine import _pick_product_photos
        all_bags = [
            {"file_id": "miumiu1", "filename": "сумка Miu Miu Arcadie 1.jpg"},
            {"file_id": "ysl1", "filename": "Сумка черная Yves Saint Laurent Monogram.jpg"},
            {"file_id": "chanel25_1", "filename": "Сумка черная Chanel 25 1.jpg"},
        ]
        picked = _pick_product_photos(all_bags, requested_color="черные")
        filenames = [p["filename"] for p in picked]
        # Только черные
        for f in filenames:
            assert "черная" in f.lower() or "черные" in f.lower(), f"Non-black photo in result: {f}"


# ── H) Category Browsing Detection ──────────────────────────────────────────


class TestCategoryBrowsing:
    """_is_category_browsing и _detect_browsing_category."""

    def test_sumki_detected(self):
        from ai.engine import _is_category_browsing, _detect_browsing_category
        assert _is_category_browsing("какие сумки у вас есть") is True
        assert _detect_browsing_category("какие сумки у вас есть") == "bag"

    def test_krossovki_detected(self):
        from ai.engine import _is_category_browsing, _detect_browsing_category
        assert _is_category_browsing("покажите кроссовки") is True
        assert _detect_browsing_category("покажите кроссовки") == "shoes"

    def test_tufli_detected(self):
        from ai.engine import _is_category_browsing, _detect_browsing_category
        assert _is_category_browsing("какие туфли есть") is True
        assert _detect_browsing_category("какие туфли есть") == "shoes"

    def test_baletki_detected(self):
        from ai.engine import _is_category_browsing, _detect_browsing_category
        assert _is_category_browsing("есть балетки?") is True
        assert _detect_browsing_category("есть балетки?") == "shoes"

    def test_specific_product_not_category(self):
        from ai.engine import _is_category_browsing
        assert _is_category_browsing("хочу Chanel 25") is False

    def test_greeting_not_category(self):
        from ai.engine import _is_category_browsing
        assert _is_category_browsing("Здравствуйте") is False

    def test_size_answer_not_category(self):
        from ai.engine import _is_category_browsing
        assert _is_category_browsing("38 размер") is False


# ── I) Should Use Active Product Query ──────────────────────────────────────


class TestShouldUseActiveProductQuery:
    """_should_use_active_product_query: когда использовать текущий товар vs новый запрос."""

    def test_category_word_returns_false(self):
        """'какие сумки есть' с активным товаром → False (новый запрос)."""
        from ai.engine import _should_use_active_product_query
        result = _should_use_active_product_query(
            "какие сумки у вас есть",
            "Golden Goose Super-Star"
        )
        assert result is False

    def test_same_product_returns_false(self):
        """Сообщение содержит токены текущего товара → False."""
        from ai.engine import _should_use_active_product_query
        result = _should_use_active_product_query(
            "а какие цвета Golden Goose?",
            "Golden Goose Super-Star"
        )
        assert result is False

    def test_brand_hint_returns_false(self):
        """Упоминание бренда → False (другой товар)."""
        from ai.engine import _should_use_active_product_query
        result = _should_use_active_product_query(
            "а есть Chanel?",
            "Jimmy Choo Azia 95"
        )
        assert result is False

    def test_generic_question_returns_true(self):
        """'какой размер?' без категории/бренда → True (вопрос о текущем товаре)."""
        from ai.engine import _should_use_active_product_query
        result = _should_use_active_product_query(
            "какой размер мне подойдет?",
            "Jimmy Choo Azia 95"
        )
        assert result is True
