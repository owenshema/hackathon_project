"""Ingest normalized messages into the raw store and queue for embedding."""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Chunk, Message
from app.schemas.memory import NormalizedMessage
from app.services.embeddings import embed_and_store_message, embed_texts


async def ingest_messages(
    db: AsyncSession, messages: list[NormalizedMessage]
) -> list[Message]:
    stored: list[Message] = []
    for nm in messages:
        offset = nm.metadata.get("meeting_offset_sec")
        if offset is not None:
            try:
                offset = float(offset)
            except (TypeError, ValueError):
                offset = None

        row = Message(
            platform=nm.platform.value,
            source_type=nm.source_type.value,
            external_id=nm.external_id,
            conversation_id=nm.conversation_id,
            author_id=nm.author_id,
            author_name=nm.author_name,
            text=nm.text,
            timestamp=datetime.fromisoformat(nm.timestamp.replace("Z", "+00:00")),
            media_url=nm.media_url,
            media_mime=nm.media_mime,
            meeting_offset_sec=offset,
            extra=nm.metadata,
        )
        db.add(row)
        stored.append(row)

    await db.flush()
    chunks: list[Chunk] = []
    for row in stored:
        media_note = ""
        if row.media_url:
            media_label = row.media_mime or row.source_type or "media"
            media_note = f"\n[Attached media: {media_label}. URL/id available to the system.]"

        vectors = embed_texts([row.text])
        chunks.append(
            Chunk(
                message_id=row.id,
                content=f"{row.text}{media_note}",
                embedding=vectors[0] if vectors else None,
                platform=row.platform,
                author_name=row.author_name,
                timestamp=row.timestamp,
                meeting_offset_sec=row.meeting_offset_sec,
                meta={
                    "source_type": row.source_type,
                    "external_id": row.external_id,
                    "conversation_id": row.conversation_id,
                    "author_id": row.author_id,
                    "media_url": row.media_url,
                    "media_mime": row.media_mime,
                    "has_media": bool(row.media_url),
                },
            )
        )
    if chunks:
        db.add_all(chunks)
    await db.commit()
    return stored
