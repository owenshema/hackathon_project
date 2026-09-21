"""Embedding helpers — local model optional; keyword RAG is the fast default."""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Chunk

if TYPE_CHECKING:
    from app.db.models import Message


@lru_cache
def _get_model():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(settings.embedding_model)


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    # Skip heavy model load for WhatsApp/Teams speed (keyword retrieval instead)
    if not settings.use_local_embeddings:
        return [[0.0] * 384 for _ in texts]
    try:
        model = _get_model()
        vectors = model.encode(texts, normalize_embeddings=True)
        return [v.tolist() for v in vectors]
    except Exception:
        return [[0.0] * 384 for _ in texts]


async def embed_and_store_message(db: AsyncSession, message: "Message") -> Chunk:
    media_note = ""
    if message.media_url:
        media_label = message.media_mime or message.source_type or "media"
        media_note = f"\n[Attached media: {media_label}. URL/id available to the system.]"

    vectors = embed_texts([message.text])
    chunk = Chunk(
        message_id=message.id,
        content=f"{message.text}{media_note}",
        embedding=vectors[0] if vectors else None,
        platform=message.platform,
        author_name=message.author_name,
        timestamp=message.timestamp,
        meeting_offset_sec=message.meeting_offset_sec,
        meta={
            "source_type": message.source_type,
            "external_id": message.external_id,
            "conversation_id": message.conversation_id,
            "media_url": message.media_url,
            "media_mime": message.media_mime,
            "has_media": bool(message.media_url),
        },
    )
    db.add(chunk)
    await db.commit()
    await db.refresh(chunk)
    return chunk
