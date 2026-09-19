"""Route a user utterance → command → answer → platform reply text."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.schemas.memory import CatchUpResponse, MemoryAnswer, Platform
from app.services.commands import parse_command
from app.services.extraction import catch_me_up
from app.services.rag import answer_question, list_decisions


def format_answer_for_platform(answer: MemoryAnswer, platform: Platform) -> str:
    """Compact text suitable for WhatsApp / Teams (keep it short for mobile)."""
    mobile = platform in {Platform.WHATSAPP, Platform.TEAMS}
    lines = [answer.answer]
    if answer.decision:
        lines.append(f"\nDecision: {answer.decision}")
    if answer.reason and not mobile:
        lines.append(f"Reason: {answer.reason}")
    if answer.evidence:
        lines.append("\nEvidence:")
        limit = 2 if mobile else 5
        excerpt_len = 100 if mobile else 180
        for ev in answer.evidence[:limit]:
            bits = [f"• {ev.source_label}"]
            if ev.author:
                bits.append(f"from {ev.author}")
            if ev.meeting_offset_display:
                bits.append(f"@ {ev.meeting_offset_display}")
            lines.append(" ".join(bits))
            if ev.excerpt:
                lines.append(f'  "{ev.excerpt[:excerpt_len]}"')
            if ev.replay_url and platform == Platform.WEB:
                lines.append(f"  Replay: {ev.replay_url}")
    if answer.confidence == "insufficient":
        lines.append("\n(Insufficient evidence — I won't guess.)")
    text = "\n".join(lines)
    # WhatsApp hard limit safety
    if mobile and len(text) > 3500:
        text = text[:3490] + "…"
    return text


def format_catchup_for_platform(recap: CatchUpResponse) -> str:
    sections = []
    if recap.important:
        sections.append("Important\n" + "\n".join(f"• {x}" for x in recap.important[:5]))
    if recap.decisions:
        sections.append("Decisions\n" + "\n".join(f"• {x}" for x in recap.decisions[:5]))
    if recap.discussions:
        sections.append(
            "Discussions\n" + "\n".join(f"• {x}" for x in recap.discussions[:4])
        )
    if recap.action_items:
        sections.append(
            "Action items\n" + "\n".join(f"• {x}" for x in recap.action_items[:5])
        )
    if not sections:
        return "Nothing new since you were last active — you're caught up."
    return "Your Catch Me Up\n\n" + "\n\n".join(sections)


async def handle_user_message(
    db: AsyncSession,
    *,
    text: str,
    user_id: str,
    user_name: str | None = None,
    platform: Platform = Platform.WEB,
    mode: str | None = None,
    conversation_id: str | None = None,
    exclude_message_ids: set | None = None,
    require_evidence: bool = False,
) -> tuple[MemoryAnswer | CatchUpResponse, str]:
    parsed = parse_command(text)
    cmd = parsed.command or "ask"
    query = parsed.query or text
    fast = platform in {Platform.WHATSAPP, Platform.TEAMS} and settings.platform_fast_mode

    if cmd == "catchup":
        recap = await catch_me_up(db, user_id, user_name=user_name)
        return recap, format_catchup_for_platform(recap)

    if cmd == "decisions":
        answer = await list_decisions(db, query)
        return answer, format_answer_for_platform(answer, platform)

    if cmd in {"ask", "meeting", "find", "changed", "tasks"}:
        effective_mode = mode
        if cmd == "tasks":
            effective_mode = "tasks"
        if cmd == "changed":
            query = query or "What changed recently?"
        if fast and not effective_mode:
            effective_mode = "30sec"
        answer = await answer_question(
            db,
            query,
            mode=effective_mode,
            fast=fast,
            conversation_id=conversation_id,
            exclude_message_ids=exclude_message_ids,
            require_evidence=require_evidence,
        )
        answer.command = cmd
        return answer, format_answer_for_platform(answer, platform)

    answer = await answer_question(
        db,
        text,
        mode=mode,
        fast=fast,
        conversation_id=conversation_id,
        exclude_message_ids=exclude_message_ids,
        require_evidence=require_evidence,
    )
    return answer, format_answer_for_platform(answer, platform)
