from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Chunk, Decision, Message
from app.db.session import get_db
from app.schemas.memory import NormalizedMessage, Platform, SourceType
from app.services.ingestion import ingest_messages
from app.services.meetings import ingest_transcript_file

router = APIRouter()


@router.post("/ingest/message")
async def ingest_chat_message(
    text: str = Form(...),
    author_name: str = Form("Unknown"),
    author_id: str = Form("manual"),
    conversation_id: str = Form("web-import"),
    platform: str = Form("web"),
    db: AsyncSession = Depends(get_db),
):
    nm = NormalizedMessage(
        platform=Platform(platform) if platform in Platform._value2member_map_ else Platform.WEB,
        source_type=SourceType.CHAT,
        external_id=f"manual-{datetime.now(timezone.utc).timestamp()}",
        conversation_id=conversation_id,
        author_id=author_id,
        author_name=author_name,
        text=text,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    stored = await ingest_messages(db, [nm])
    return {"ingested": len(stored), "ids": [str(m.id) for m in stored]}


@router.post("/ingest/transcript")
async def ingest_transcript(
    title: str = Form(...),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    content = (await file.read()).decode("utf-8", errors="replace")
    meeting = await ingest_transcript_file(
        db, title=title, content=content, filename=file.filename
    )
    return {
        "meeting_id": str(meeting.id),
        "title": meeting.title,
        "status": meeting.status,
    }


@router.post("/ingest/clear-mock")
async def clear_mock_data(db: AsyncSession = Depends(get_db)):
    """Remove seeded/demo mock rows so memory comes only from real chats."""
    demo_externals = ["demo-wa-1", "demo-wa-2", "demo-mtg-1", "demo-voice-1"]
    ids = (
        await db.execute(
            select(Message.id).where(
                or_(
                    Message.external_id.in_(demo_externals),
                    Message.conversation_id == "unipods-group",
                    Message.conversation_id.like("meeting:UniPods%"),
                    Message.external_id.like("demo-%"),
                    Message.external_id.like("TESTMSG%"),
                )
            )
        )
    ).scalars().all()

    deleted_chunks = 0
    deleted_messages = 0
    if ids:
        c = await db.execute(delete(Chunk).where(Chunk.message_id.in_(ids)))
        deleted_chunks = c.rowcount or 0
        m = await db.execute(delete(Message).where(Message.id.in_(ids)))
        deleted_messages = m.rowcount or 0

    await db.execute(
        delete(Decision).where(Decision.context.ilike("%Extracted from recent%"))
    )
    await db.commit()
    return {
        "cleared": True,
        "deleted_messages": deleted_messages,
        "deleted_chunks": deleted_chunks,
    }


@router.post("/ingest/sync")
async def sync_whatsapp_messages(db: AsyncSession = Depends(get_db)):
    """
    Pull the latest WhatsApp messages from Wassenger and ingest any that
    are not yet in the database, sorted chronologically (oldest → newest).
    Useful to call manually any time to bring memory fully up to date.
    """
    from app.services.sync import sync_recent_whatsapp_messages
    added = await sync_recent_whatsapp_messages(db, limit=200)
    return {
        "status": "ok",
        "new_messages_ingested": added,
        "message": (
            f"Sync complete. {added} new message(s) added to memory."
            if added else
            "Memory is already fully up-to-date. No new messages found."
        ),
    }
