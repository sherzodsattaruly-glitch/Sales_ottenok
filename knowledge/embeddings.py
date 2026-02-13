"""
Генерация эмбеддингов и хранение в ChromaDB.
"""

import logging
import chromadb
from openai import OpenAI

from config import CHROMA_DB_PATH, OPENAI_API_KEY, OPENAI_EMBEDDING_MODEL

logger = logging.getLogger(__name__)

openai_client = OpenAI(api_key=OPENAI_API_KEY)
chroma_client = chromadb.PersistentClient(path=CHROMA_DB_PATH)


def generate_embeddings_batch(texts: list[str]) -> list[list[float]]:
    """Генерировать эмбеддинги для пакета текстов."""
    response = openai_client.embeddings.create(
        input=texts,
        model=OPENAI_EMBEDDING_MODEL,
    )
    return [item.embedding for item in response.data]


def store_in_collection(collection_name: str, chunks: list[dict]):
    """Сохранить чанки с эмбеддингами в коллекцию ChromaDB."""
    collection = chroma_client.get_or_create_collection(collection_name)

    batch_size = 50  # лимит OpenAI embedding API
    total = 0

    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        texts = [c["text"] for c in batch]

        # Фильтруем metadata — ChromaDB принимает только str, int, float, bool
        metadatas = []
        for c in batch:
            clean_meta = {}
            for k, v in c.get("metadata", {}).items():
                if isinstance(v, (str, int, float, bool)):
                    clean_meta[k] = v
            metadatas.append(clean_meta)

        ids = [f"{collection_name}_{i + j}" for j in range(len(batch))]

        embeddings = generate_embeddings_batch(texts)

        collection.add(
            ids=ids,
            documents=texts,
            embeddings=embeddings,
            metadatas=metadatas,
        )
        total += len(batch)
        logger.info(f"  Stored batch {i // batch_size + 1}: {len(batch)} chunks")

    logger.info(f"Total stored in '{collection_name}': {total} chunks")


def delete_collection(name: str):
    """Удалить коллекцию ChromaDB."""
    try:
        chroma_client.delete_collection(name)
        logger.info(f"Deleted collection '{name}'")
    except Exception:
        pass
