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
    if since is None:
        activity = await db.scalar(
            select(UserActivity).where(UserActivity.user_id == user_id)
        )
        since = activity.last_seen_at if activity else None

    q = select(Message).order_by(Message.timestamp.desc()).limit(50)
    if since is not None:
        q = (
            select(Message)
            .where(Message.timestamp >= since)
            .order_by(Message.timestamp.asc())
            .limit(100)
        )
    result = await db.execute(q)
    messages = list(result.scalars().all())
    texts = [
        f"[{m.timestamp.isoformat()}] {m.author_name or m.author_id}: {m.text}"
        for m in messages
    ]
    response = await extract_from_texts(db, texts, evidence_messages=messages)

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
    return response
