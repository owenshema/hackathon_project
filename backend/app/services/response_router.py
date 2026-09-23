"""Route a user utterance → command → answer → platform reply text."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.schemas.memory import CatchUpResponse, MemoryAnswer, Platform
from app.services.commands import parse_command
from app.services.extraction import catch_me_up
from app.services.rag import answer_question, list_decisions
from app.services.voice_recap import (
    VOICE_RECAP_OFFER_WHATSAPP,
    cache_catchup_script,
    catchup_to_voice_script,
    get_saved_voice_answer,
    is_voice_recap_request,
)


def _author_mention(author: str | None) -> str:
    if not author:
        return "@someone"

    clean = author.strip()
    if clean.startswith("meeting:"):
        clean = clean.replace("meeting:", "").strip()
    if clean in {"UNIPOD TASK GROUP", "UNIPODS COHORT 1"}:
        return "a cohort member"
    if clean.startswith("@"):
        return clean

    first_name = clean.split()[0].strip("():,")
    known = {
        "joel": "@Joel",
        "joe": "@Joel",
        "shema": "@Shema",
        "owen": "@Owen",
        "deborah": "@Deborah",
        "kgosi": "@Kgosi",
        "reitumetse": "@Reitumetse",
    }
    return known.get(first_name.lower(), f"@{first_name}" if first_name else "@someone")


def format_answer_for_platform(answer: MemoryAnswer, platform: Platform) -> str:
    """Clean text suitable for WhatsApp / Teams, matching meti_bot style."""
    mobile = platform in {Platform.WHATSAPP, Platform.TEAMS}
    if mobile:
        # Meti_bot delivers direct, clean answers without robotic evidence dumps
        text = (answer.answer or "").strip()
        if len(text) > 3500:
            text = text[:3490] + "…"
        return text

    lines = [answer.answer]
    if answer.decision:
        lines.append(f"\nDecision: {answer.decision}")
    if answer.reason:
        lines.append(f"Reason: {answer.reason}")
    if answer.evidence:
        lines.append("\nEvidence:")
        for ev in answer.evidence[:5]:
            author = ev.author or "an official source"
            excerpt = ev.excerpt or ""
            lines.append(f"• {author}: \"{excerpt[:180]}\"")
            if ev.replay_url and platform == Platform.WEB:
                lines.append(f"  Replay: {ev.replay_url}")
    return "\n".join(lines)


def format_catchup_for_platform(
    recap: CatchUpResponse, platform: Platform = Platform.WEB
) -> str:
    sections = []
    if recap.summary:
        sections.append("*Today so far*\n" + recap.summary)
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
    body = "*Today's group catch-up*\n\n" + "\n\n".join(sections)
    if platform == Platform.WHATSAPP:
        body += VOICE_RECAP_OFFER_WHATSAPP
    return body


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
    import re

    # Pre-clean filler words: "can you a tag ..." → "can you tag ..."
    text_clean = re.sub(r'\b(can you|could you|please)\s+a\s+', r'\1 ', text, flags=re.I)
    # Also handle bare "a tag" / "a tell" at start of sentence
    text_clean = re.sub(r'\ba\s+(tag|tell|ask|remind|notify|ping)\b', r'\1', text_clean, flags=re.I)

    # ── Pin message handler ──────────────────────────────────────────────────
    pin_match = re.search(
        r'(?:can you |please )?(?:pin|unpin)\s+(?:this\s+)?(?:message\s+)?["\']?(.+?)["\']?\s*$',
        text_clean,
        flags=re.I,
    )
    # Also extract quoted text anywhere in a pin request
    if not pin_match:
        pin_match = re.search(
            r'(?:pin|unpin)\b.*?["\u201c\u2018]([^"\']+)["\u201d\u2019]',
            text_clean,
            flags=re.I,
        )
    if pin_match or re.search(r'\b(pin|unpin)\s+(this|the)?\s*(message|msg)?\b', text_clean, flags=re.I):
        # Extract the message to be pinned — prefer quoted content
        quoted = re.search(r'["\u201c\u2018]([^"\']+)["\u201d\u2019]', text_clean)
        if quoted:
            pinned_content = quoted.group(1).strip()
        elif pin_match:
            raw_content = pin_match.group(1).strip()
            # Remove trailing noise words
            raw_content = re.sub(r'\b(message|msg|this|the|it)\s*$', '', raw_content, flags=re.I).strip()
            pinned_content = raw_content if raw_content else None
        else:
            pinned_content = None

        if pinned_content:
            pin_text = f"📌 *Pinned Message*\n\n{pinned_content}"
        else:
            pin_text = "📌 *Pinned:* I couldn't find the message content to pin. Please quote the text you'd like pinned."
        pin_answer = MemoryAnswer(answer=pin_text, confidence="high", evidence=[])
        return pin_answer, format_answer_for_platform(pin_answer, platform)
    # ────────────────────────────────────────────────────────────────────────

    # Direct action request handler (e.g. "tag joel asking him how he is doing so far", "tag owen to create google meet")
    # Only fires for known team members or broadcast words — prevents garbage like "Hey @me, ..."
    KNOWN_ACTION_TARGETS = {
        "owen", "shema", "joel", "joe", "deborah", "kgosi", "reitumetse",
        "everyone", "all", "team", "everybody",
    }
    action_match = re.search(
        r"(?:can you |could you |please )?(?:tag|tell|ask|remind|notify|ping)\s+([A-Za-z0-9_@+]+)\s*(?:to|and (?:ask|tell) (?:him|her|them) to|asking (?:him|her|them)|telling (?:him|her|them)|that)?\s*(.*)",
        text_clean,
        flags=re.I,
    )
    if action_match:
        raw_target = action_match.group(1).strip().lower()
        instruction = action_match.group(2).strip()
        # Reject unknown targets (not a team member, not a phone number, not broadcast)
        is_phone = re.match(r'^\+?[\d]{7,}$', raw_target)
        if raw_target not in KNOWN_ACTION_TARGETS and not is_phone:
            action_match = None  # Fall through to RAG

    if action_match:
        raw_target = action_match.group(1).strip().lower()
        instruction = action_match.group(2).strip()

        # --- Broadcast target detection ---
        BROADCAST_TARGETS = {"everyone", "all", "team", "everybody", "all@", "everyone@"}
        if raw_target in BROADCAST_TARGETS:
            # Clean up instruction: strip leading connectors
            msg = re.sub(r'^(?:and\s+)?(?:tell|to\s+tell|that\s+)', '', instruction, flags=re.I).strip()
            msg = re.sub(r'^(?:there\'?s\s+|that\s+there\'?s\s+)', "there's ", msg, flags=re.I)
            if msg and not msg[0].isupper():
                msg = msg[0].upper() + msg[1:]
            if msg and not msg.endswith(('.', '!', '?')):
                msg += '.'
            broadcast_text = f"@all 📢 {msg}" if msg else "@all 📢 Attention everyone!"
            broadcast_answer = MemoryAnswer(
                answer=broadcast_text,
                confidence="high",
                evidence=[],
            )
            return broadcast_answer, format_answer_for_platform(broadcast_answer, platform)

        TEAM_TAGS = {
            "owen": "@250782972679 (Owen)",
            "shema": "@250782972679 (Shema Owen)",
            "joel": "@250789201681 (Joel)",
            "joe": "@250789201681 (Joel)",
            "deborah": "@Deborah",
            "kgosi": "@Kgosi",
            "reitumetse": "@Reitumetse",
        }
        tag = None
        for k, v in TEAM_TAGS.items():
            if k in raw_target:
                tag = v
                break
        if not tag:
            tag = f"@{raw_target.lstrip('@')}"

        if instruction:
            # Transform third-person indirect speech into natural, direct second-person speech
            s = instruction
            s = re.sub(r'^(?:and\s+)?(?:ask|asking|tell|telling)\s+(?:him|her|them)\s+(?:to\s+)?', '', s, flags=re.I)
            s = re.sub(r'^(?:to|that)\s+', '', s, flags=re.I)

            # Inversions: (question words + pronoun + is/are doing) -> (question word + are you doing)
            s = re.sub(r'\b(how|what|where|why|when)\s+(?:he|she|they)\s+(?:is|are)\s+doing\b', r'\1 are you doing', s, flags=re.I)
            s = re.sub(r'\b(how|what|where|why|when)\s+(?:he|she|they)\s+is\b', r'\1 are you', s, flags=re.I)
            s = re.sub(r'\b(how|what|where|why|when)\s+(?:he|she|they)\s+are\b', r'\1 are you', s, flags=re.I)
            s = re.sub(r'\b(how|what|where|why|when)\s+(?:he|she|they)\s+(?:will|can|could|would)\b', r'\1 can you', s, flags=re.I)

            # If conditions
            s = re.sub(r'\bif\s+(?:he|she)\s+has\b', 'have you', s, flags=re.I)
            s = re.sub(r'\bif\s+(?:he|she)\s+is\b', 'are you', s, flags=re.I)
            s = re.sub(r'\bif\s+(?:he|she)\s+can\b', 'can you', s, flags=re.I)

            # Pronoun replacements
            replacements = [
                (r'\b(?:he|she)\s+is\b', 'you are'),
                (r'\b(?:he|she)\s+was\b', 'you were'),
                (r'\b(?:he|she)\s+has\b', 'you have'),
                (r'\b(?:he|she)\s+can\b', 'you can'),
                (r'\b(?:he|she)\b', 'you'),
                (r'\b(?:him|her)\b', 'you'),
                (r'\b(?:his|her|hers)\b', 'your'),
            ]
            for pattern, repl in replacements:
                s = re.sub(pattern, repl, s, flags=re.I)

            s = s.strip()
            # If imperative verb, make it courteous
            if re.match(r'^(?:create|make|send|start|schedule|work|check|update|join|review|push)\b', s, flags=re.I):
                if not s.lower().startswith('please'):
                    s = 'please ' + s

            # Proper punctuation
            if s and not s.endswith(('?', '!', '.')):
                if re.match(r'^(?:how|what|where|when|why|have|are|can|is|did|do|will)\b', s, flags=re.I):
                    s += '?'
                else:
                    s += '.'

            action_text = f"Hey {tag}, {s}"
        else:
            action_text = f"Hey {tag}, the team is calling your attention!"

        action_answer = MemoryAnswer(
            answer=action_text,
            confidence="high",
            evidence=[],
        )
        return action_answer, format_answer_for_platform(action_answer, platform)

    parsed = parse_command(text)
    cmd = parsed.command or "ask"
    query = parsed.query or text
    fast = platform in {Platform.WHATSAPP, Platform.TEAMS} and settings.platform_fast_mode

    if is_voice_recap_request(text):
        # Prefer the user's last actual answer in this chat.  This lets every
        # answer be read aloud, including after the web service has restarted.
        script = await get_saved_voice_answer(db, user_id, conversation_id)
        if not script:
            recap = await catch_me_up(
                db,
                user_id,
                user_name=user_name,
                conversation_id=conversation_id,
                exclude_message_ids=exclude_message_ids,
            )
            script = catchup_to_voice_script(recap)
            cache_catchup_script(user_id, conversation_id, script)
        if not script.strip():
            empty = MemoryAnswer(
                answer=(
                    "I don't have a recent catch-up to read aloud yet. "
                    "Ask me to *catch you up* first, then tag me for a *voice recap*."
                ),
                confidence="high",
                evidence=[],
            )
            return empty, format_answer_for_platform(empty, platform)
        voice_answer = MemoryAnswer(
            answer="Sending your voice recap 🎙",
            confidence="high",
            evidence=[],
            deliver_voice_recap=True,
            voice_recap_script=script,
        )
        return voice_answer, format_answer_for_platform(voice_answer, platform)

    if cmd == "catchup":
        recap = await catch_me_up(
            db,
            user_id,
            user_name=user_name,
            conversation_id=conversation_id,
            exclude_message_ids=exclude_message_ids,
        )
        script = catchup_to_voice_script(recap)
        cache_catchup_script(user_id, conversation_id, script)
        return recap, format_catchup_for_platform(recap, platform)

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
