"""Voice notes — TTS of the latest bot reply, including catch-up recaps."""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import VoiceAnswer
from app.schemas.memory import CatchUpResponse

VOICE_RECAP_OFFER_WHATSAPP = (
    "\n\n🎙 Want this as a voice message? Reply *give me this as a voice message*."
)

_VOICE_OFFER_STRIP = re.compile(
    r"(?:🎙\s*)?Want (?:this|these messages) as a voice (?:note|message)\?.*$",
    re.I | re.S,
)

VOICE_RECAP_TTL_SECONDS = 3600

# Questions *about* voice notes must never trigger TTS delivery.
_VOICE_QUESTION_PATTERNS = [
    re.compile(
        r"\b(?:can|do|does|could)\s+you\s+(?:understand|transcribe|hear|listen(?:\s+to)?)\b.*\bvoice\b",
        re.I,
    ),
    re.compile(r"\bwhat\s+is\s+a\s+voice\s+(?:message|note)\b", re.I),
    re.compile(
        r"\b(?:what|who|when|where|why|how|which|did|does|is|are|was|were)\b.{0,80}\bvoice\s*(?:message|note|recap)?\b",
        re.I,
    ),
    re.compile(
        r"\bvoice\s*(?:message|note|recap)\b.{0,80}\b(?:mean|about|from|by|say|said|contain)\b",
        re.I,
    ),
]

# Only clear "please send/read this as audio" intents — never a bare "voice message" keyword.
_VOICE_RECAP_PATTERNS = [
    re.compile(r"\bvoice\s*recap\b", re.I),
    re.compile(r"\baudio\s+recap\b", re.I),
    re.compile(r"\bcan\s+(?:it|this)\s+be\s+(?:the\s+)?vn\b", re.I),
    re.compile(r"^\s*(?:vn|voicenote)\s*[!.?]*\s*$", re.I),
    re.compile(r"\bsend\s+(?:me\s+)?(?:a\s+|the\s+)?voice(?:\s+(?:message|note))?\b", re.I),
    re.compile(r"\bsend\s+(?:me\s+)?(?:a\s+|the\s+)?(?:voice|audio)\s+(?:message|note|recap)\b", re.I),
    re.compile(
        r"\b(?:give|send|share)\s+(?:me\s+)?(?:this|that|it)?\s*(?:as|in)\s+(?:a\s+)?(?:voice|audio)\b",
        re.I,
    ),
    re.compile(r"\b(?:this|that|it)\s+as\s+(?:a\s+)?(?:voice|audio)(?:\s+(?:message|note))?\b", re.I),
    re.compile(r"\bas\s+a\s+voice\s+(?:message|note)\b", re.I),
    re.compile(r"\bread\s+(?:it|this|the\s+summary)\s+(?:out|aloud|to\s+me)\b", re.I),
    re.compile(
        r"\bread\s+(?:(?:that|this|the|my|your)\s+)?(?:last\s+)?(?:[\w-]+\s+){0,5}answer\s+(?:out|aloud|to\s+me)\b",
        re.I,
    ),
]


@dataclass
class _CachedRecap:
    script: str
    expires_at: float


_cache: dict[str, _CachedRecap] = {}


def _cache_key(user_id: str, conversation_id: str | None) -> str:
    conv = conversation_id or "direct"
    return f"{user_id}:{conv}"


def cache_catchup_script(
    user_id: str,
    conversation_id: str | None,
    script: str,
) -> None:
    if not script.strip():
        return
    key = _cache_key(user_id, conversation_id)
    _cache[key] = _CachedRecap(
        script=script.strip(),
        expires_at=time.time() + VOICE_RECAP_TTL_SECONDS,
    )


def get_cached_catchup_script(
    user_id: str,
    conversation_id: str | None,
) -> str | None:
    key = _cache_key(user_id, conversation_id)
    entry = _cache.get(key)
    if not entry:
        return None
    if time.time() > entry.expires_at:
        _cache.pop(key, None)
        return None
    return entry.script


async def save_voice_answer(
    db: AsyncSession,
    user_id: str,
    conversation_id: str | None,
    text: str,
) -> None:
    """Persist the latest answer so voice requests survive service restarts."""
    script = recap_text_to_voice_script(text)
    if not script.strip():
        return
    conversation = conversation_id or "direct"
    db.add(
        VoiceAnswer(
            user_id=user_id,
            conversation_id=conversation,
            script=script,
        )
    )
    await db.commit()
    # Keep the memory cache too, for the fastest path in the current process.
    cache_catchup_script(user_id, conversation_id, script)


async def get_saved_voice_answer(
    db: AsyncSession,
    user_id: str,
    conversation_id: str | None,
) -> str | None:
    """Get the latest answer for this person and chat, including after restart."""
    cached = get_cached_catchup_script(user_id, conversation_id)
    if cached:
        return cached
    conversation = conversation_id or "direct"
    row = await db.scalar(
        select(VoiceAnswer.script)
        .where(
            VoiceAnswer.user_id == user_id,
            VoiceAnswer.conversation_id == conversation,
        )
        .order_by(VoiceAnswer.created_at.desc())
        .limit(1)
    )
    if row:
        cache_catchup_script(user_id, conversation_id, row)
    return row


def is_voice_recap_request(text: str) -> bool:
    raw = (text or "").strip()
    raw = re.sub(r"^@\S+\s+", "", raw)
    if not raw:
        return False
    if any(p.search(raw) for p in _VOICE_QUESTION_PATTERNS):
        return False
    return any(p.search(raw) for p in _VOICE_RECAP_PATTERNS)


def catchup_to_voice_script(recap: CatchUpResponse) -> str:
    """Spoken script from the same sections shown in text catch-up."""
    parts: list[str] = ["Here is your group catch-up."]
    if recap.summary:
        parts.append(_strip_for_speech(recap.summary))
    if recap.recent_messages:
        parts.append("Here are the latest messages.")
        for item in recap.recent_messages:
            parts.append(_strip_for_speech(item))
    if recap.important:
        parts.append("Important.")
        for item in recap.important[:5]:
            parts.append(_strip_for_speech(item))
    if recap.decisions:
        parts.append("Decisions.")
        for item in recap.decisions[:5]:
            parts.append(_strip_for_speech(item))
    if recap.discussions:
        parts.append("Discussion.")
        for item in recap.discussions[:4]:
            parts.append(_strip_for_speech(item))
    if recap.action_items:
        parts.append("Action items.")
        for item in recap.action_items[:5]:
            parts.append(_strip_for_speech(item))
    if len(parts) == 1:
        parts.append("Nothing major was recorded in recent group activity.")
    script = " ".join(p for p in parts if p)
    return script[:3500]


def recap_text_to_voice_script(text: str) -> str:
    """Spoken script from the last bot reply (not recap-specific)."""
    without_offer = _VOICE_OFFER_STRIP.sub("", text or "").strip()
    cleaned = _strip_for_speech(without_offer)
    if not cleaned:
        return "I could not build a voice note from that message."
    return cleaned[:3500]


def _strip_for_speech(text: str) -> str:
    s = text or ""
    s = re.sub(r"\*+", "", s)
    s = re.sub(r"[_~`]", "", s)
    s = re.sub(r"📢|💬|✅|🎙|📌|🏆|•", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


async def synthesize_voice_note(script: str) -> Path | None:
    """Render MP3 via edge-tts; returns file path or None if TTS unavailable."""
    script = _strip_for_speech(script)
    if not script:
        return None

    try:
        import edge_tts
    except ImportError:
        print("[VoiceRecap] edge-tts not installed — pip install edge-tts")
        return None

    out_dir = Path(settings.upload_dir) / "voice_recaps"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"recap_{uuid.uuid4().hex[:12]}.mp3"

    voice = settings.tts_voice or "en-US-JennyNeural"
    communicate = edge_tts.Communicate(script, voice)
    await communicate.save(str(out_path))
    if not out_path.exists() or out_path.stat().st_size == 0:
        return None
    return out_path
