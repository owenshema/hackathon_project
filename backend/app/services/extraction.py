"""Single extraction pass: decisions + action items + catch-up sections."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ActionItem, Decision, Message, UserActivity
from app.schemas.memory import CatchUpResponse, EvidenceItem
from app.services.evidence import evidence_from_message
from app.services.llm import generate_json

EXTRACT_SYSTEM = """You extract structured community memory from chat/meeting text.
Return JSON with keys:
  important: string[]
  decisions: [{decision, reason, author}]
  discussions: string[]
  action_items: [{task, assignee}]
Only use facts present in the text. Empty arrays are fine.
"""

DAILY_CATCHUP_SYSTEM = """You write a clear, useful daily catch-up for a WhatsApp group.

Use only facts in the supplied messages from today. Reason across the full
conversation: connect related messages, distinguish questions from answers,
and identify the actual progress, decisions, blockers, deadlines, and next
steps. Do not invent details or treat an unanswered question as a decision.

Return JSON only with these keys:
{
  "summary": "A concise 2–5 sentence account of what has happened today.",
  "important": ["Key announcements, deadlines, or blockers"],
  "decisions": ["Decisions that were actually made or confirmed"],
  "discussions": ["The most important ongoing discussion points"],
  "action_items": ["Specific next steps and owners, only where stated"]
}

Every array may be empty. Keep the entire response under 180 words and make
it useful to someone who has missed the day's conversation."""

# CAT is UTC+2 year-round. A fixed offset avoids requiring the optional IANA
# timezone database on Windows Python installations.
GROUP_TIMEZONE = timezone(timedelta(hours=2), name="CAT")


def _message_transcript(messages: list[Message]) -> str:
    lines: list[str] = []
    for message in messages:
        text = " ".join((message.text or "").split())
        if not text:
            continue
        author = message.author_name or message.author_id or "Someone"
        timestamp = message.timestamp.astimezone(GROUP_TIMEZONE).strftime("%H:%M") if message.timestamp else ""
        lines.append(f"[{timestamp}] {author}: {text}")
    return "\n".join(lines)


def _as_lines(value: object, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()][:limit]


async def extract_from_texts(
    db: AsyncSession,
    texts: list[str],
    *,
    evidence_messages: list[Message] | None = None,
) -> CatchUpResponse:
    joined = "\n---\n".join(texts[:40])
    if not joined.strip():
        return CatchUpResponse()

    data = await generate_json(
        f"Extract community memory from:\n\n{joined}",
        EXTRACT_SYSTEM,
    )

    evidence: list[EvidenceItem] = []
    if evidence_messages:
        for msg in evidence_messages[:5]:
            evidence.append(evidence_from_message(msg))

    # Persist decisions / action items
    for d in data.get("decisions") or []:
        if isinstance(d, dict) and d.get("decision"):
            db.add(
                Decision(
                    decision=d["decision"],
                    reason=d.get("reason"),
                    decided_at=datetime.now(timezone.utc),
                    context="Extracted from recent activity",
                    authors=[d["author"]] if d.get("author") else [],
                    evidence_message_id=(
                        evidence_messages[0].id if evidence_messages else None
                    ),
                )
            )
    for a in data.get("action_items") or []:
        if isinstance(a, dict) and a.get("task"):
            db.add(
                ActionItem(
                    task=a["task"],
                    assignee_name=a.get("assignee"),
                    evidence_message_id=(
                        evidence_messages[0].id if evidence_messages else None
                    ),
                )
            )
    await db.commit()

    return CatchUpResponse(
        important=list(data.get("important") or []),
        decisions=[
            d["decision"] if isinstance(d, dict) else str(d)
            for d in (data.get("decisions") or [])
        ],
        discussions=list(data.get("discussions") or []),
        action_items=[
            (f"{a.get('assignee')}: {a['task']}" if isinstance(a, dict) and a.get("assignee") else (a["task"] if isinstance(a, dict) else str(a)))
            for a in (data.get("action_items") or [])
        ],
        evidence=evidence,
    )


async def catch_me_up(
    db: AsyncSession,
    user_id: str,
    *,
    user_name: str | None = None,
    since: datetime | None = None,
    conversation_id: str | None = None,
    exclude_message_ids: set | None = None,
) -> CatchUpResponse:
    """
    Summarize all conversation from the start of today through this request.

    The requested command message is excluded when supplied by the webhook
    handler. Time is interpreted in CAT (Africa/Johannesburg), the programme's
    operating timezone.
    """
    now = datetime.now(GROUP_TIMEZONE)
    day_start_utc = now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    stmt = (
        select(Message)
        .where(Message.timestamp >= day_start_utc)
        .order_by(Message.timestamp.asc())
    )
    if conversation_id:
        stmt = stmt.where(Message.conversation_id == conversation_id)
    result = await db.execute(stmt)
    excluded = exclude_message_ids or set()
    messages = [m for m in result.scalars().all() if m.id not in excluded]
    transcript = _message_transcript(messages)
    if transcript:
        data = await generate_json(
            f"Today is {now.strftime('%A, %d %B %Y')} (CAT). "
            f"Summarize the group activity from midnight through {now.strftime('%H:%M')} CAT.\n\n"
            f"Messages:\n{transcript}",
            DAILY_CATCHUP_SYSTEM,
        )
        summary = str(data.get("summary") or data.get("answer") or "").strip()
        if not summary:
            summary = "Today's group conversation has been recorded, but I could not produce a reliable summary yet."
        important = _as_lines(data.get("important"), 5)
        decisions = _as_lines(data.get("decisions"), 5)
        discussions = _as_lines(data.get("discussions"), 4)
        action_items = _as_lines(data.get("action_items"), 5)
    else:
        summary = "There have not been any group messages yet today."
        important = decisions = discussions = action_items = []

    # Update last seen
    activity = await db.scalar(
        select(UserActivity).where(UserActivity.user_id == user_id)
    )
    now = datetime.now(timezone.utc)
    if activity:
        activity.last_seen_at = now
        activity.user_name = user_name or activity.user_name
    else:
        db.add(
            UserActivity(
                user_id=user_id,
                user_name=user_name,
                last_seen_at=now,
            )
        )
    await db.commit()

    evidence: list[EvidenceItem] = [evidence_from_message(m) for m in messages[-5:]]

    return CatchUpResponse(
        summary=summary,
        important=important,
        decisions=decisions,
        discussions=discussions,
        action_items=action_items,
        evidence=evidence,
    )

