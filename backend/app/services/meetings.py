"""Meeting transcript / audio upload → timestamped segments → RAG."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Meeting, Message
from app.schemas.memory import NormalizedMessage, Platform, SourceType
from app.services.ingestion import ingest_messages

# Matches "Speaker Name 00:12:34" or "[00:12:34] Speaker:" or "00:12 Speaker:"
TIMESTAMP_LINE = re.compile(
    r"^(?:\[)?(?:(?P<h>\d{1,2}):)?(?P<m>\d{1,2}):(?P<s>\d{2})(?:\])?"
    r"\s*(?:[-–—]\s*)?(?P<speaker>[A-Za-z][\w\s.-]{0,40})?\s*[:|]?\s*(?P<text>.+)$"
)


def _parse_offset(h: str | None, m: str, s: str) -> float:
    hours = int(h or 0)
    return hours * 3600 + int(m) * 60 + int(s)


def parse_transcript(text: str, meeting_title: str) -> list[NormalizedMessage]:
    messages: list[NormalizedMessage] = []
    now = datetime.now(timezone.utc).isoformat()
    conversation_id = f"meeting:{meeting_title}"

    for i, raw_line in enumerate(text.splitlines()):
        line = raw_line.strip()
        if not line:
            continue
        match = TIMESTAMP_LINE.match(line)
        if match:
            offset = _parse_offset(match.group("h"), match.group("m"), match.group("s"))
            speaker = (match.group("speaker") or "Unknown").strip()
            body = match.group("text").strip()
        else:
            offset = float(i * 5)  # coarse fallback spacing
            speaker = "Unknown"
            body = line

        messages.append(
            NormalizedMessage(
                platform=Platform.UPLOAD,
                source_type=SourceType.MEETING,
                external_id=f"{conversation_id}:{i}",
                conversation_id=conversation_id,
                author_id=speaker.lower().replace(" ", "_"),
                author_name=speaker,
                text=body,
                timestamp=now,
                metadata={"meeting_offset_sec": offset, "meeting_title": meeting_title},
            )
        )
    return messages


async def ingest_transcript_file(
    db: AsyncSession,
    *,
    title: str,
    content: str,
    filename: str | None = None,
) -> Meeting:
    upload_root = Path(settings.upload_dir)
    upload_root.mkdir(parents=True, exist_ok=True)
    path = upload_root / f"{uuid4()}_{filename or 'transcript.txt'}"
    path.write_text(content, encoding="utf-8")

    meeting = Meeting(
        title=title,
        platform="upload",
        started_at=datetime.now(timezone.utc),
        transcript_path=str(path),
        status="processing",
    )
    db.add(meeting)
    await db.commit()
    await db.refresh(meeting)

    normalized = parse_transcript(content, title)
    # Attach meeting id into metadata for later replay
    for nm in normalized:
        nm.metadata["meeting_id"] = str(meeting.id)

    stored = await ingest_messages(db, normalized)
    # Backfill meeting_offset on Message rows (already set via metadata in ingest)
    for row, nm in zip(stored, normalized):
        row.meeting_offset_sec = nm.metadata.get("meeting_offset_sec")
        row.extra = {**row.extra, "meeting_id": str(meeting.id)}
    meeting.status = "ready"
    await db.commit()
    return meeting
