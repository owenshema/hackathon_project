"""Detect when a WhatsApp group message needs clarification from community memory."""

from __future__ import annotations

import re

# Phrases that signal someone is asking for info already shared / unclear or giving an action
CLARIFY_PATTERNS = [
    r"\?",
    r"^/(ask|catchup|decisions|tasks|meeting|find|changed|help)\b",
    r"\b(tag|tell|ask|remind|notify|ping|alert)\s+([A-Za-z0-9_@+]+)\b",
    r"\b(can you|could you|please)\s+(tag|tell|ask|remind|notify|ping|pin|help|clarify|answer)\b",
    r"\b(create|schedule|make|start|send)\s+(a |the )?(meet|meeting|call|google meet|link)\b",
    r"\b(pin|unpin)\s+(this|the)?\s*(message|msg)?\b",
    r"\bwhat (did|was|is|are|about|happened|happend|happen)\b",
    r"\bwho (said|decided|suggested|proposed|is|are|was|were)\b",
    r"\bwhen (is|was|do|did|are|will)\b",
    r"\bwhy (did|do|are|was)\b",
    r"\bhow (do|did|can|much|many|to)\b",
    r"\bwhere (is|was|are|do|can)\b",
    r"\bwhich (one|option|model|database|room)\b",
    r"\b(is|are|will|can|do|does|did|should|could|would)\s+(there|we|it|anyone|somebody|the)\b",
    r"\bremind me\b",
    r"\bcan someone (clarify|explain|confirm|tell)\b",
    r"\bneed clarification\b",
    r"\bclarify\b",
    r"\bi('m| am) (confused|lost|unclear)\b",
    r"\bwhat (was|were) (the |our )?(decision|deadline|plan|agreement|time|date)\b",
    r"\bdid we (agree|decide|choose|confirm)\b",
    r"\bhave we (agreed|decided|chosen)\b",
    r"\balready (discussed|decided|said|agreed)\b",
    r"\bcatch me up\b",
    r"\bwhat did i miss\b",
    r"\banyone know\b",
    r"\bdoes anyone (know|remember)\b",
    r"\bplease confirm\b",
    r"\bwas it (decided|confirmed|agreed)\b",
    r"\bany update(s)?\b",
    r"\bany news\b",
]

_COMPILED = [re.compile(p, re.I) for p in CLARIFY_PATTERNS]


def needs_clarification(text: str, *, is_group: bool = False, was_mentioned: bool = False) -> bool:
    """
    True when the message is asking for information that may already
    exist in shared group / meeting memory, an action request, or when the bot is directly mentioned.
    """
    raw = (text or "").strip()
    if not raw or raw.startswith("["):
        return False

    # If the bot was directly @mentioned in a group, always attempt to answer
    if was_mentioned:
        return True

    # Check for question/clarification/action patterns
    if any(p.search(raw) for p in _COMPILED):
        return True

    # Treat natural action requests (even without tags)
    lower = raw.lower()
    if lower.startswith(("ask ", "tell ", "tag ", "remind ", "explain", "help", "can you", "could you", "please ")):
        return True

    return False


def check_and_strip_bot_mention(
    text: str, bot_names: list[str] | None = None
) -> tuple[str, bool]:
    """Remove @mentions of the bot from group messages and detect if bot was tagged."""
    cleaned = text or ""
    was_mentioned = False
    for name in bot_names or []:
        if not name:
            continue
        pattern = rf"@{re.escape(name)}\b"
        if re.search(pattern, cleaned, flags=re.I):
            was_mentioned = True
            cleaned = re.sub(pattern, "", cleaned, flags=re.I)

    # If not already matched, detect ANY @mention in the message (e.g. @joe, @bot, etc.)
    if not was_mentioned and "@" in cleaned:
        mention_match = re.search(r"@(\w+)", cleaned)
        if mention_match:
            was_mentioned = True
            cleaned = re.sub(r"@\w+", "", cleaned)

    return cleaned.strip(), was_mentioned


def strip_bot_mention(text: str, bot_names: list[str] | None = None) -> str:
    """Remove @mentions of the bot from group messages."""
    cleaned, _ = check_and_strip_bot_mention(text, bot_names)
    return cleaned
