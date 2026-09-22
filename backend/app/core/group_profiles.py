"""Per-group settings for isolated WhatsApp bot behaviour."""

from __future__ import annotations

import re


TEST_GROUP_ID = "120363429618850959@g.us"

GROUP_PROFILES: dict[str, dict] = {
    TEST_GROUP_ID: {
        "name": "UniPods METI AI Program 2026 Cohort",
        "admins": {
            "250786387244": "Munira",
            "250783188656": "Diane",
            "250789355992": "Jeovaire",
            "263774094822": "Gift",
        },
        # Never combine this group's answers with another group's messages or
        # documents. The supplied export is the starting memory for this group.
        "strict_isolation": True,
    },
}


def normalize_phone(value: str | None) -> str:
    return "".join(re.findall(r"\d", value or ""))


def profile_for(conversation_id: str | None) -> dict | None:
    return GROUP_PROFILES.get(conversation_id or "")


def is_group_admin(
    conversation_id: str | None,
    *,
    author_id: str | None = None,
    author_name: str | None = None,
) -> bool:
    profile = profile_for(conversation_id)
    if not profile:
        return False
    admins = profile["admins"]
    if normalize_phone(author_id) in admins:
        return True
    clean_name = (author_name or "").strip().lower()
    return clean_name in {name.lower() for name in admins.values()}


def admin_names(conversation_id: str | None) -> str:
    profile = profile_for(conversation_id)
    if not profile:
        return ""
    return ", ".join(profile["admins"].values())
