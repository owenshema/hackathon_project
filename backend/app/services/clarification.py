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
    # Modal questions must begin the message.  Without the anchor, ordinary
    # discussion such as "we can do it" is mistaken for the question "do it".
    r"^\s*(?:is|are|will|can|does|did|should|could|would)\s+(?:there|we|it|anyone|somebody|the)\b",
    r"^\s*do\s+(?:we|they|i|you)\b",
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
    r"\bvoice\s*(?:recap|note|message)\b",
    r"\bsend\s+(?:me\s+)?(?:the\s+)?voice\b",
    r"\bwhat did i miss\b",
    r"\banyone know\b",
    r"\bdoes anyone (know|remember)\b",
    r"\bplease confirm\b",
    r"\bwas it (decided|confirmed|agreed)\b",
    r"\bany update(s)?\b",
    r"\bany news\b",
]

_COMPILED = [re.compile(p, re.I) for p in CLARIFY_PATTERNS]

GREETING_PATTERNS = [
    r"^\s*(hi|hello|hey|good morning|good afternoon|good evening|thanks|thank you|ok|okay)\s*[!.]*\s*$",
    r"^\s*(hi|hello|hey)\s+(everyone|team|guys|all)\s*[!.]*\s*$",
]

IMPORTANT_UNANSWERED_PATTERNS = [
    r"\b(deadline|due date|submit|submission|apply|application|form|register|registration)\b",
    r"\b(urgent|important|asap|immediately|right now|today|tomorrow)\b",
    r"\b(meeting|session|call|google meet|link|venue|location|time)\b",
    r"\b(requirement|required|must|need to|have to|should we|what do i need)\b",
    r"\b(decision|decide|agreed|confirmed|approval|permission)\b",
    r"\b(error|blocked|stuck|can't|cannot|unable|not working|issue|problem)\b",
    r"\b(payment|prize|certificate|selection|winner|team name|github|repository)\b",
]

CURIOSITY_PATTERNS = [
    r"\b(just curious|curious|wondering|what is|what are|why is|why are|how does|tell me about)\b",
    r"\b(explain|learn|understand|meaning|means)\b",
]

_GREETINGS = [re.compile(p, re.I) for p in GREETING_PATTERNS]
_IMPORTANT_UNANSWERED = [re.compile(p, re.I) for p in IMPORTANT_UNANSWERED_PATTERNS]
_CURIOSITY = [re.compile(p, re.I) for p in CURIOSITY_PATTERNS]


def is_greeting_or_smalltalk(text: str) -> bool:
    raw = (text or "").strip()
    return bool(raw and any(p.search(raw) for p in _GREETINGS))


def is_important_unanswered_question(text: str) -> bool:
    """
    True when an unanswered message should be surfaced to admins instead of ignored.
    This avoids tagging admins for greetings and low-stakes curiosity.
    """
    raw = (text or "").strip()
    if not raw or is_greeting_or_smalltalk(raw):
        return False

    lower = raw.lower()
    has_question_shape = "?" in raw or any(
        lower.startswith(prefix)
        for prefix in (
            "what",
            "when",
            "where",
            "who",
            "why",
            "how",
            "can",
            "could",
            "should",
            "is",
            "are",
            "do",
            "does",
            "did",
        )
    )
    if not has_question_shape:
        return False

    important = any(p.search(raw) for p in _IMPORTANT_UNANSWERED)
    curious = any(p.search(raw) for p in _CURIOSITY)
    urgent_even_if_curious = bool(
        re.search(
            r"\b(deadline|due date|submit|submission|urgent|asap|today|tomorrow|meeting|session|required|must|blocked|stuck|can't|cannot|unable|not working)\b",
            raw,
            flags=re.I,
        )
    )
    if curious and not urgent_even_if_curious:
        return False
    if important:
        return True

    # Unanswered direct requests for confirmation can block group progress.
    return bool(re.search(r"\b(confirm|clarify|help|answer|update)\b", raw, flags=re.I))


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
