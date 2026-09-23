"""Introduce the bot once when it is added to a WhatsApp group."""

from __future__ import annotations

import re
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import GroupWelcome

WASSENGER_API = "https://api.wassenger.com/v1"
WASSENGER_EVENTS = ["message:in:new", "group:update"]

GROUP_WELCOME_TEXT = (
    "👋 Hi everyone — I'm *JOTDS bot*, your UniPods group memory assistant "
    "from team *JOTDS*.\n\n"
    "I follow this chat so you can ask what was said, decided, or missed "
    "instead of scrolling.\n\n"
    "*How I work*\n"
    "1. Tag *@JOTDS bot* and ask a question "
    "(deadlines, decisions, links, who said what).\n"
    "2. Ask me to *catch you up* for a recap of recent group activity.\n"
    "3. After any answer, reply *give me this as a voice message* "
    "and I will send that answer as audio.\n"
    "4. When someone shares useful info, I credit and tag them — "
    "I will not invent answers.\n"
    "5. If I do not have it in the group memory, I will say so rather than guess.\n\n"
    "That's it — tag me anytime."
)

_ADDED_PATTERNS = [
    re.compile(r"\byou were added\b", re.I),
    re.compile(r"\badded you\b", re.I),
    re.compile(r"\bjoined using a group link\b", re.I),
]


def _digits(value: str | None) -> str:
    return "".join(ch for ch in (value or "") if ch.isdigit())


def _bot_phone_digits() -> str:
    return _digits(settings.wassenger_phone)


def extract_group_id(payload: dict[str, Any]) -> str | None:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    chat = data.get("chat") if isinstance(data.get("chat"), dict) else {}
    candidates = [
        chat.get("id"),
        data.get("to"),
        data.get("chatId"),
        data.get("from"),
        payload.get("to"),
        payload.get("chatId"),
    ]
    for raw in candidates:
        value = str(raw or "").strip()
        if value.endswith("@g.us"):
            return value
    return None


def bot_was_added_to_group(payload: dict[str, Any]) -> bool:
    event = str(payload.get("event") or "")
    bot = _bot_phone_digits()
    if not bot:
        return False

    if event == "group:update":
        action = str(payload.get("action") or payload.get("type") or "").lower()
        if action and action != "add":
            return False
        people = payload.get("participants") or data_participants(payload)
        for person in people:
            if bot in _digits(str(person)):
                return True
        return False

    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    body = str(data.get("body") or data.get("message") or payload.get("body") or "")
    return any(pattern.search(body) for pattern in _ADDED_PATTERNS)


def data_participants(payload: dict[str, Any]) -> list[Any]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    raw = payload.get("participants") or data.get("participants") or []
    return raw if isinstance(raw, list) else []


async def claim_group_welcome(db: AsyncSession, conversation_id: str) -> bool:
    """Return True if this process should send the intro (first time only)."""
    existing = await db.scalar(
        select(GroupWelcome.id).where(GroupWelcome.conversation_id == conversation_id)
    )
    if existing:
        return False
    db.add(GroupWelcome(conversation_id=conversation_id))
    try:
        await db.commit()
        return True
    except IntegrityError:
        await db.rollback()
        return False


async def maybe_send_group_welcome(
    db: AsyncSession,
    adapter: Any,
    *,
    conversation_id: str | None,
    payload: dict[str, Any] | None = None,
) -> bool:
    group_id = conversation_id or (extract_group_id(payload) if payload else None)
    if not group_id or not str(group_id).endswith("@g.us"):
        return False
    if not await claim_group_welcome(db, group_id):
        return False

    delivery = await adapter.send_reply(
        conversation_id=group_id,
        text=GROUP_WELCOME_TEXT,
        metadata={"reply_kind": "group", "reply_to": group_id},
    )
    print(
        f"[WhatsApp] Group welcome group={group_id} "
        f"ok={delivery.get('ok')} mode={delivery.get('mode')}"
    )
    if delivery.get("ok"):
        return True
    row = await db.scalar(
        select(GroupWelcome).where(GroupWelcome.conversation_id == group_id)
    )
    if row:
        await db.delete(row)
        await db.commit()
    return False


async def ensure_group_update_webhook() -> None:
    """Subscribe the existing Wassenger webhook to group join events."""
    api_key = settings.wassenger_api_key
    if not api_key:
        return
    headers = {"Token": api_key, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(f"{WASSENGER_API}/webhooks", headers=headers)
        if response.status_code != 200:
            print(f"[Wassenger] Could not list webhooks ({response.status_code})")
            return
        hooks = response.json()
        if not isinstance(hooks, list):
            return
        for hook in hooks:
            url = str(hook.get("url") or "")
            if "/api/v1/webhooks/whatsapp" not in url:
                continue
            events = list(hook.get("events") or [])
            if "group:update" in events:
                continue
            merged = list(dict.fromkeys([*events, *WASSENGER_EVENTS]))
            hook_id = hook.get("id")
            payload = {
                "name": hook.get("name") or "JOTDS bot",
                "url": url,
                "events": merged,
            }
            patch = await client.patch(
                f"{WASSENGER_API}/webhooks/{hook_id}",
                headers=headers,
                json=payload,
            )
            print(
                f"[Wassenger] Webhook {hook_id} events -> {merged} "
                f"status={patch.status_code}"
            )
