"""Single extraction pass: decisions + action items + catch-up sections."""

from __future__ import annotations

from datetime import datetime, timezone

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
) -> CatchUpResponse:
    """
    Build a catch-up summary from recent group activity.
    Always pulls the last 50 messages + stored decisions + action items
    so the user always gets a meaningful summary regardless of last_seen_at.
    """
    from app.db.models import ActionItem as ActionItemModel, Decision as DecisionModel

    # Pull recent messages (always last 50, regardless of last_seen timestamp)
    result = await db.execute(
        select(Message).order_by(Message.timestamp.desc()).limit(50)
    )
    messages = list(reversed(result.scalars().all()))  # chronological order

    # Pull stored decisions (last 5)
    dec_result = await db.execute(
        select(DecisionModel).order_by(DecisionModel.created_at.desc()).limit(5)
    )
    decisions_db = list(dec_result.scalars().all())

    # Pull stored action items (last 5)
    act_result = await db.execute(
        select(ActionItemModel).order_by(ActionItemModel.created_at.desc()).limit(5)
    )
    action_items_db = list(act_result.scalars().all())

    # Build sections directly from DB — no LLM needed
    important: list[str] = []
    discussions: list[str] = []

    # Scan messages for important content (announcements, deadlines, links, tasks)
    IMPORTANT_KEYWORDS = (
        "deadline", "submit", "submission", "important", "urgent", "meeting",
        "join", "google meet", "link", "call", "reminder", "don't forget",
        "unipod", "prize", "hackathon",
    )
    seen_texts: set[str] = set()
    for m in messages:
        text = (m.text or "").strip()
        if not text or len(text) < 10:
            continue
        key = text[:60].lower()
        if key in seen_texts:
            continue
        seen_texts.add(key)
        author = m.author_name or m.author_id or "Someone"
        line = f"{author}: {text[:120]}"
        if any(kw in text.lower() for kw in IMPORTANT_KEYWORDS):
            important.append(line)
        else:
            discussions.append(line)

    # Cap sections
    important = important[:5]
    discussions = discussions[:4]

    # Decisions from DB
    decision_lines = [
        d.decision + (f" ({d.reason})" if d.reason else "")
        for d in decisions_db
    ]

    # Action items from DB
    action_lines = [
        (f"{a.assignee_name}: {a.task}" if a.assignee_name else a.task)
        for a in action_items_db
    ]

    # If we have messages but no structured data yet, build from messages directly
    if not decision_lines and not action_lines and not important and not discussions:
        if messages:
            discussions = [
                f"{(m.author_name or m.author_id or 'Someone')}: {(m.text or '')[:100]}"
                for m in messages[-5:]
            ]

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

    evidence: list[EvidenceItem] = [evidence_from_message(m) for m in messages[-3:]]

    return CatchUpResponse(
        important=important,
        decisions=decision_lines,
        discussions=discussions,
        action_items=action_lines,
        evidence=evidence,
    )

