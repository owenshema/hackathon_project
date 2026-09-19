"""Ingest normalized messages into the raw store and queue for embedding."""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Message
from app.schemas.memory import NormalizedMessage
from app.services.embeddings import embed_and_store_message


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

    await db.commit()
    for row in stored:
        await db.refresh(row)
        await embed_and_store_message(db, row)

    return stored
