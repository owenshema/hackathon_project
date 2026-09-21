"""Evidence formatting & replay URL helpers."""

from pathlib import Path
import re

from app.db.models import Chunk, Message
from app.schemas.memory import EvidenceItem, EvidenceKind


def friendly_media_filename(media_url: str | None, text: str | None = None) -> str | None:
    if text and "[attached file:" in text.lower():
        visible = re.split(r"\[attached file:", text, maxsplit=1, flags=re.I)[0].strip()
        if visible:
            return visible.splitlines()[0].strip()
    if not media_url:
        return None
    name = Path(media_url).name
    return re.sub(r"^\d{5,}[-_\s]+", "", name)


def format_offset(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    total = int(seconds)
    m, s = divmod(total, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def evidence_from_message(message: Message, excerpt: str | None = None) -> EvidenceItem:
    kind = EvidenceKind.WHATSAPP_MESSAGE
    if message.platform == "teams":
        kind = EvidenceKind.TEAMS_MESSAGE
    if message.source_type == "voice":
        kind = EvidenceKind.WHATSAPP_VOICE
    if message.source_type == "meeting" or message.meeting_offset_sec is not None:
        kind = EvidenceKind.MEETING_TIMESTAMP

    label = {
        EvidenceKind.WHATSAPP_MESSAGE: "WhatsApp discussion",
        EvidenceKind.WHATSAPP_VOICE: "WhatsApp voice message",
        EvidenceKind.TEAMS_MESSAGE: "Teams discussion",
        EvidenceKind.MEETING_TIMESTAMP: "Teams meeting",
        EvidenceKind.DOCUMENT_SECTION: "Document",
    }[kind]

    offset = format_offset(message.meeting_offset_sec)
    replay = None
    if message.media_url and offset:
        replay = f"/api/v1/evidence/replay?meeting_media={message.media_url}&t={message.meeting_offset_sec}"
    elif message.media_url and message.source_type == "voice":
        replay = f"/api/v1/evidence/replay?voice={message.id}"
    elif message.id:
        replay = f"/api/v1/evidence/{message.id}"

    return EvidenceItem(
        kind=kind,
        source_label=label,
        author=message.author_name,
        timestamp=message.timestamp.isoformat() if message.timestamp else None,
        meeting_offset_sec=message.meeting_offset_sec,
        meeting_offset_display=offset,
        excerpt=(excerpt or message.text)[:500],
        message_id=message.id,
        replay_url=replay,
        media_url=message.media_url,
        media_mime=message.media_mime,
        media_filename=friendly_media_filename(message.media_url, message.text),
    )


def evidence_from_chunk(chunk: Chunk) -> EvidenceItem:
    source_type = (chunk.meta or {}).get("source_type")
    meta = chunk.meta or {}
    kind = EvidenceKind.WHATSAPP_MESSAGE
    if chunk.platform == "teams":
        kind = EvidenceKind.TEAMS_MESSAGE
    if source_type == "voice":
        kind = EvidenceKind.WHATSAPP_VOICE
    if chunk.meeting_offset_sec is not None:
        kind = EvidenceKind.MEETING_TIMESTAMP

    offset = format_offset(chunk.meeting_offset_sec)
    label = {
        EvidenceKind.WHATSAPP_MESSAGE: "Chat",
        EvidenceKind.WHATSAPP_VOICE: "WhatsApp voice message",
        EvidenceKind.TEAMS_MESSAGE: "Teams discussion",
        EvidenceKind.MEETING_TIMESTAMP: "Meeting",
        EvidenceKind.DOCUMENT_SECTION: "Document",
    }[kind]
    return EvidenceItem(
        kind=kind,
        source_label=label,
        author=meta.get("shared_by") or chunk.author_name,
        timestamp=chunk.timestamp.isoformat() if chunk.timestamp else None,
        meeting_offset_sec=chunk.meeting_offset_sec,
        meeting_offset_display=offset,
        excerpt=chunk.content[:500],
        message_id=chunk.message_id,
        meeting_id=chunk.meeting_id,
        replay_url=(
            f"/api/v1/evidence/replay?meeting_id={chunk.meeting_id}&t={chunk.meeting_offset_sec}"
            if chunk.meeting_id and chunk.meeting_offset_sec is not None
            else (f"/api/v1/evidence/{chunk.message_id}" if chunk.message_id else None)
        ),
        media_url=meta.get("media_url"),
        media_mime=meta.get("media_mime"),
        media_filename=friendly_media_filename(
            str(meta.get("media_url")) if meta.get("media_url") else None,
            chunk.content,
        ),
    )
