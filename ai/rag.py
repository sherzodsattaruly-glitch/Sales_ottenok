"""
RAG-пайплайн: поиск по ChromaDB.
"""

import logging
import chromadb
from openai import AsyncOpenAI

from config import CHROMA_DB_PATH, OPENAI_API_KEY, OPENAI_EMBEDDING_MODEL, MAX_RAG_RESULTS

logger = logging.getLogger(__name__)

openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
chroma_client = chromadb.PersistentClient(path=CHROMA_DB_PATH)


async def get_embedding(text: str) -> list[float]:
    """Сгенерировать эмбеддинг для текстового запроса."""
    response = await openai_client.embeddings.create(
        input=text,
        model=OPENAI_EMBEDDING_MODEL,
    )
    return response.data[0].embedding


def _format_results(results) -> list[dict]:
    """Преобразовать результаты ChromaDB в список словарей."""
    if not results or not results["documents"] or not results["documents"][0]:
        return []

    formatted = []
    for i, doc in enumerate(results["documents"][0]):
        meta = results["metadatas"][0][i] if results["metadatas"] else {}
        formatted.append({"text": doc, "metadata": meta})
    return formatted


async def search_products(query: str, n_results: int = MAX_RAG_RESULTS) -> list[dict]:
    """
    Поиск товаров в каталоге.

    ИЗМЕНЕНО: Теперь использует Google Sheets каталог вместо ChromaDB.
    """
    from catalog.sheets_loader import search_catalog, format_product_for_prompt

    try:
        products = search_catalog(query, max_results=n_results)

        # Форматируем в формат, ожидаемый engine.py
        formatted = []
        for product in products:
            formatted.append({
                "text": format_product_for_prompt(product),
                "metadata": {
                    "name": product.get("name", ""),
                    "category": product.get("category", ""),
                    "price": product.get("price", ""),
                    "colors": product.get("colors", ""),
                }
            })

        return formatted

    except Exception as e:
        logger.error(f"Ошибка поиска товаров в Google Sheets каталоге: {e}")
        return []


async def search_scripts(query: str, n_results: int = 3) -> list[dict]:
    """Поиск в скриптах продаж и примерах переписок."""
    try:
        collection = chroma_client.get_collection("sales_scripts")
    except Exception:
        logger.warning("Коллекция sales_scripts не найдена")
        return []

    query_embedding = await get_embedding(query)
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=n_results,
    )
    return _format_results(results)
