"""Shared recap formatting — structured fallback when LLM summarization fails."""

from __future__ import annotations

import re

from app.db.models import Message

ADMIN_NAME_FRAGMENTS = frozenset(
    {
        "shema",
        "owen",
        "joel",
        "diane",
        "gift",
        "munira",
        "jeovaire",
        "charles",
    }
)

IMPORTANT_KEYWORDS = (
    "deadline",
    "submit",
    "submission",
    "important",
    "urgent",
    "meeting",
    "join",
    "google meet",
    "link",
    "call",
    "reminder",
    "don't forget",
    "unipod",
    "prize",
    "hackathon",
    "announce",
    "official",
    "must",
    "required",
)

DECISION_ACTION_PATTERNS = [
    re.compile(r"\b(we agreed|agreed to|decided to|decision is|confirmed that)\b", re.I),
    re.compile(r"\b(will|need to|has to|please)\s+\w", re.I),
    re.compile(r"\b(action item|assigned to|responsible for)\b", re.I),
    re.compile(r"\b(by monday|by tuesday|by wednesday|by thursday|by friday|by tomorrow|by today|eod|asap)\b", re.I),
]


def _author_label(message: Message) -> str:
    return message.author_name or message.author_id or "Someone"


def is_admin_author(name: str | None) -> bool:
    if not name:
        return False
    lower = name.lower()
    return any(fragment in lower for fragment in ADMIN_NAME_FRAGMENTS)


def _one_line_summary(author: str, text: str, *, max_len: int = 88) -> str:
    """Single-line bullet content — trimmed quote, not invented paraphrase."""
    compact = " ".join(text.split())
    if len(compact) > max_len:
        compact = compact[: max_len - 3].rstrip() + "..."
    return f"- {author}: {compact}"


def _is_decision_or_action(text: str) -> bool:
    return any(p.search(text) for p in DECISION_ACTION_PATTERNS)


def structured_recap_from_messages(messages: list[Message], *, max_words: int = 150) -> str:
    """
    Evidence-only recap in the same WhatsApp section layout as the LLM prompt.
    Uses message text verbatim (trimmed), classified by speaker role and keywords.
    """
    admin_lines: list[str] = []
    decision_lines: list[str] = []
    discussion_lines: list[str] = []
    seen: set[str] = set()

    for message in messages:
        text = (message.text or "").strip()
        if not text or text.startswith("[") or len(text) < 3:
            continue
        dedupe_key = text[:80].lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        author = _author_label(message)
        lower = text.lower()
        line = _one_line_summary(author, text)

        if is_admin_author(author) or any(kw in lower for kw in IMPORTANT_KEYWORDS):
            if line not in admin_lines:
                admin_lines.append(line)
            continue

        if _is_decision_or_action(text):
            if line not in decision_lines:
                decision_lines.append(line)
            continue

        if len(text) >= 18 and "?" not in text[-3:]:
            if line not in discussion_lines:
                discussion_lines.append(line)

    parts: list[str] = ["📢 *From the admins:*"]
    if admin_lines:
        parts.extend(admin_lines[:4])
    else:
        parts.append("No admin messages in this batch.")

    if discussion_lines:
        parts.append("")
        parts.append("💬 *Discussion:*")
        parts.extend(discussion_lines[:3])

    combined_actions = decision_lines[:4]
    if combined_actions:
        parts.append("")
        parts.append("✅ *Decisions or action items:*")
        parts.extend(combined_actions)

    body = "\n".join(parts)
    words = body.split()
    if len(words) > max_words:
        body = " ".join(words[:max_words]) + "..."
    return body
