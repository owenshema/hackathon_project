"""SQLAlchemy models — raw message store + decisions + meetings + chunks."""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, DateTime, Float, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.config import settings

# Prefer Postgres UUID/JSONB; use pgvector only when enabled
if settings.use_sqlite:
    from sqlalchemy import Uuid as UUIDType

    JsonType = JSON
    EmbeddingType = JSON  # list[float] stored as JSON
elif settings.use_pgvector:
    from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
    from pgvector.sqlalchemy import Vector

    UUIDType = PGUUID(as_uuid=True)
    JsonType = JSONB
    EmbeddingType = Vector(384)
else:
    # Postgres without pgvector extension (common on Windows)
    from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID

    UUIDType = PGUUID(as_uuid=True)
    JsonType = JSONB
    EmbeddingType = JSONB


class Base(DeclarativeBase):
    pass


class Message(Base):
    """Persistent citation source for every chat / voice / meeting segment."""

    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, primary_key=True, default=uuid.uuid4
    )
    platform: Mapped[str] = mapped_column(String(32), index=True)
    source_type: Mapped[str] = mapped_column(String(32), index=True)
    external_id: Mapped[str] = mapped_column(String(255), index=True)
    conversation_id: Mapped[str] = mapped_column(String(255), index=True)
    author_id: Mapped[str] = mapped_column(String(255), index=True)
    author_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    media_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    media_mime: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    meeting_offset_sec: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    extra: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Meeting(Base):
    __tablename__ = "meetings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(String(512))
    platform: Mapped[str] = mapped_column(String(32), default="upload")
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    audio_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    transcript_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    extra: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Decision(Base):
    """Structured decisions extracted in the same pass as Catch Me Up."""

    __tablename__ = "decisions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, primary_key=True, default=uuid.uuid4
    )
    decision: Mapped[str] = mapped_column(Text)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    decided_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    context: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    evidence_message_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUIDType, nullable=True
    )
    evidence_meeting_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUIDType, nullable=True
    )
    evidence_offset_sec: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    authors: Mapped[list] = mapped_column(JsonType, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ActionItem(Base):
    __tablename__ = "action_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, primary_key=True, default=uuid.uuid4
    )
    task: Mapped[str] = mapped_column(Text)
    assignee_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    assignee_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    due_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), default="open")
    evidence_message_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUIDType, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Chunk(Base):
    """Embedded text chunks for RAG (pgvector on Postgres, JSON on SQLite)."""

    __tablename__ = "chunks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, primary_key=True, default=uuid.uuid4
    )
    message_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUIDType, nullable=True, index=True
    )
    meeting_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUIDType, nullable=True, index=True
    )
    content: Mapped[str] = mapped_column(Text)
    embedding = mapped_column(EmbeddingType, nullable=True)
    platform: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    author_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    timestamp: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    meeting_offset_sec: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    meta: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class UserActivity(Base):
    """Tracks last activity for personalized Catch Me Up."""

    __tablename__ = "user_activity"

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    user_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    platform: Mapped[str] = mapped_column(String(32), default="web")
