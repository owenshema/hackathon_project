from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Message
from app.db.session import get_db
from app.services.evidence import evidence_from_message, friendly_media_filename

router = APIRouter()


@router.get("/evidence/replay")
async def replay_evidence(
    meeting_id: str | None = Query(None),
    meeting_media: str | None = Query(None),
    voice: str | None = Query(None),
    t: float | None = Query(None, description="Offset in seconds"),
    db: AsyncSession = Depends(get_db),
):
    """
    Evidence Replay endpoint.
    MVP returns metadata + seek hint; frontend jumps to timestamp / plays voice.
    Stretch: stream media with Range support.
    """
    if voice:
        try:
            msg = await db.get(Message, UUID(voice))
        except ValueError:
            msg = None
        if not msg:
            raise HTTPException(status_code=404, detail="Voice evidence not found")
        return {
            "type": "voice",
            "message_id": str(msg.id),
            "author": msg.author_name,
            "media_url": msg.media_url,
            "excerpt": msg.text,
            "play": True,
        }

    return {
        "type": "meeting",
        "meeting_id": meeting_id,
        "media": meeting_media,
        "seek_seconds": t,
        "seek_display": None if t is None else f"{int(t) // 60}:{int(t) % 60:02d}",
        "play": True,
        "note": "Attach meeting audio URL in Meeting.audio_path for full replay.",
    }


@router.get("/evidence/{message_id}")
async def get_evidence(message_id: UUID, db: AsyncSession = Depends(get_db)):
    msg = await db.get(Message, message_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Evidence not found")
    return evidence_from_message(msg).model_dump(mode="json")


@router.get("/evidence/media/{message_id}/{filename}")
async def download_evidence_media(
    message_id: UUID,
    filename: str,
    db: AsyncSession = Depends(get_db),
):
    msg = await db.get(Message, message_id)
    if not msg or not msg.media_url:
        raise HTTPException(status_code=404, detail="Evidence media not found")
    path = msg.media_url
    display_name = friendly_media_filename(path, msg.text) or filename
    return FileResponse(
        path,
        media_type=msg.media_mime or "application/octet-stream",
        filename=display_name,
    )
