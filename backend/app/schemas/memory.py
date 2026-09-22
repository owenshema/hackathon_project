from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class Platform(str, Enum):
    WHATSAPP = "whatsapp"
    TEAMS = "teams"
    WEB = "web"
    UPLOAD = "upload"


class SourceType(str, Enum):
    CHAT = "chat"
    VOICE = "voice"
    MEETING = "meeting"
    DOCUMENT = "document"


class EvidenceKind(str, Enum):
    WHATSAPP_MESSAGE = "whatsapp_message"
    WHATSAPP_VOICE = "whatsapp_voice"
    TEAMS_MESSAGE = "teams_message"
    MEETING_TIMESTAMP = "meeting_timestamp"
    DOCUMENT_SECTION = "document_section"


# ---------------------------------------------------------------------------
# Normalized message (platform adapters → common schema)
# ---------------------------------------------------------------------------


class NormalizedMessage(BaseModel):
    """Single internal schema for WhatsApp, Teams, and web chat."""

    platform: Platform
    source_type: SourceType = SourceType.CHAT
    external_id: str
    conversation_id: str
    author_id: str
    author_name: Optional[str] = None
    text: str
    timestamp: str  # ISO-8601
    reply_to_id: Optional[str] = None
    media_url: Optional[str] = None
    media_mime: Optional[str] = None
    metadata: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Evidence / Answer (evidence-first responses)
# ---------------------------------------------------------------------------


class EvidenceItem(BaseModel):
    kind: EvidenceKind
    source_label: str
    author: Optional[str] = None
    timestamp: Optional[str] = None
    meeting_offset_sec: Optional[float] = None
    meeting_offset_display: Optional[str] = None  # e.g. "42:18"
    excerpt: str
    message_id: Optional[UUID] = None
    meeting_id: Optional[UUID] = None
    document_id: Optional[UUID] = None
    page_or_section: Optional[str] = None
    replay_url: Optional[str] = None
    media_url: Optional[str] = None
    media_mime: Optional[str] = None
    media_filename: Optional[str] = None


class MemoryAnswer(BaseModel):
    answer: str
    confidence: str = "medium"  # high | medium | low | insufficient
    decision: Optional[str] = None
    reason: Optional[str] = None
    evidence: list[EvidenceItem] = Field(default_factory=list)
    command: Optional[str] = None
    deliver_voice_recap: bool = False
    voice_recap_script: Optional[str] = None


class ChatRequest(BaseModel):
    question: str
    user_id: Optional[str] = None
    user_name: Optional[str] = None
    conversation_id: Optional[str] = None
    platform: Platform = Platform.WEB
    mode: Optional[str] = None  # "30sec" | "detailed" | "decisions" | "tasks"


class CatchUpRequest(BaseModel):
    user_id: str
    user_name: Optional[str] = None
    since: Optional[str] = None  # ISO-8601; default = last activity
    mode: Optional[str] = "detailed"


class CatchUpResponse(BaseModel):
    # Exact recent messages, oldest to newest.  This is deliberately separate
    # from the optional summary sections so a catch-up never hides the actual
    # conversation behind stale extracted decisions.
    recent_messages: list[str] = Field(default_factory=list)
    summary: str = ""
    important: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    discussions: list[str] = Field(default_factory=list)
    action_items: list[str] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
