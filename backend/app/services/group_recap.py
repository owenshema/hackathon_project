"""Automatic WhatsApp group recaps every N ingested messages."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.whatsapp import WhatsAppAdapter
from app.core.config import settings
from app.core.group_profiles import admin_names
from app.db.models import Message
from app.schemas.memory import NormalizedMessage
from app.services.llm import generate
from app.services.recap_format import structured_recap_from_messages

GROUP_RECAP_SYSTEM = """You summarize a batch of WhatsApp group messages for a quick catch-up.

GROUNDING RULES:
- Only summarize what is actually in the provided messages. Never infer, assume, or add information that wasn't said.
- If something is ambiguous or unclear from the messages alone, say so rather than guessing.
- Do not editorialize or add opinions — report what was said.

Treat as admins (official announcements/instructions): Shema Owen, Joel, Diane, Gift Ntuli, Munira, Jeovaire Umukundwa, Charles, and METI/program coordinators.

Prioritize: admin announcements first, then concrete decisions/tasks, then substantive member discussion. Skip pure greetings and one-word reactions unless they are the only content.

OUTPUT FORMAT:
Respond in this exact structure, using plain WhatsApp-friendly text (no markdown headers, use emoji + bold-style asterisks sparingly):

📢 *From the admins:*
- [bullet per distinct admin announcement/decision/instruction, one line each, include who said it]
(If none: "No admin messages in this batch.")

💬 *Discussion:*
- [bullet per relevant member discussion point, only if it meets the prioritization rules above]
(If none relevant: omit this section entirely)

✅ *Decisions or action items:*
- [any concrete decision or task assigned, with who's responsible if stated]
(If none: omit this section entirely)

Keep the whole recap under 150 words. Be concise — this is a quick catch-up, not a transcript.
"""


def _format_messages_for_prompt(messages: list[Message]) -> str:
    lines: list[str] = []
    for m in messages:
        text = (m.text or "").strip()
        if not text:
            continue
        author = m.author_name or m.author_id or "Unknown"
        lines.append(f"{author}: {text}")
    return "\n".join(lines)


async def _message_count(db: AsyncSession, conversation_id: str) -> int:
    result = await db.scalar(
        select(func.count())
        .select_from(Message)
        .where(Message.conversation_id == conversation_id)
    )
    return int(result or 0)


async def _last_n_messages(
    db: AsyncSession, conversation_id: str, n: int
) -> list[Message]:
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.timestamp.desc())
        .limit(n)
    )
    return list(reversed(result.scalars().all()))


async def _today_messages(db: AsyncSession, conversation_id: str) -> list[Message]:
    """All messages in this group since midnight CAT, in conversation order."""
    now = datetime.now(timezone(timedelta(hours=2), name="CAT"))
    day_start_utc = now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    result = await db.execute(
        select(Message)
        .where(
            Message.conversation_id == conversation_id,
            Message.timestamp >= day_start_utc,
        )
        .order_by(Message.timestamp.asc())
    )
    return list(result.scalars().all())


async def summarize_group_batch(db: AsyncSession, messages: list[Message]) -> str:
    transcript = _format_messages_for_prompt(messages)
    if not transcript.strip():
        return ""

    group_admins = admin_names(messages[0].conversation_id) if messages else ""
    admin_context = (
        f"For this group, the only trusted admins are: {group_admins}."
        if group_admins
        else "Use the default administrator guidance."
    )
    prompt = (
        f"These are all {len(messages)} group messages sent today so far. "
        "Write a meaningful recap of the day so far, not a recap of only the newest messages.\n\n"
        f"{admin_context}\n\n{transcript}"
    )
    for use_fast in (False, True):
        try:
            text = await generate(prompt, GROUP_RECAP_SYSTEM, fast=use_fast)
            text = (text or "").strip()
            if text and ("From the admins" in text or "📢" in text):
                return text
            if text and len(text.split()) >= 12:
                return text
        except Exception as exc:
            print(f"[GroupRecap] LLM failed (fast={use_fast}): {exc}")

    return structured_recap_from_messages(messages)


def _group_additions(candidates: list[NormalizedMessage]) -> dict[str, int]:
    added: dict[str, int] = defaultdict(int)
    for msg in candidates:
        if not (msg.metadata or {}).get("is_group"):
            continue
        if not msg.conversation_id:
            continue
        added[msg.conversation_id] += 1
    return dict(added)


def _recap_thresholds_crossed(count_before: int, count_after: int, interval: int) -> list[int]:
    if interval <= 0 or count_after <= count_before:
        return []
    first = ((count_before // interval) + 1) * interval
    thresholds: list[int] = []
    t = first
    while t <= count_after:
        thresholds.append(t)
        t += interval
    return thresholds


async def maybe_send_group_recaps(
    db: AsyncSession,
    adapter: WhatsAppAdapter,
    candidates: list[NormalizedMessage],
) -> None:
    """
    After new group messages are stored, post a recap when the conversation
    hits every `group_recap_message_interval` ingested messages (default 20).
    """
    interval = settings.group_recap_message_interval
    if interval <= 0:
        return

    additions = _group_additions(candidates)
    if not additions:
        return

    metadata_by_conv: dict[str, dict] = {}
    for msg in candidates:
        if (msg.metadata or {}).get("is_group") and msg.conversation_id:
            metadata_by_conv[msg.conversation_id] = msg.metadata or {}

    for conversation_id, added in additions.items():
        count_after = await _message_count(db, conversation_id)
        count_before = count_after - added
        thresholds = _recap_thresholds_crossed(count_before, count_after, interval)
        if not thresholds:
            continue

        metadata = metadata_by_conv.get(conversation_id, {"is_group": True})
        for _ in thresholds:
            day_messages = await _today_messages(db, conversation_id)
            recap_text = await summarize_group_batch(db, day_messages)
            if not recap_text:
                continue
            try:
                delivery = await adapter.send_reply(
                    conversation_id=conversation_id,
                    text=recap_text,
                    reply_to_id=None,
                    metadata=metadata,
                )
                print(
                    "[GroupRecap] Posted recap "
                    f"conv={conversation_id[:20]}… ok={delivery.get('ok')} "
                    f"mode={delivery.get('mode')}"
                )
            except Exception as exc:
                print(f"[GroupRecap] Send error: {exc}")
