import json
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters import get_adapter
from app.adapters.teams import TeamsAdapter
from app.adapters.whatsapp import WhatsAppAdapter
from app.core.config import settings
from app.db.session import get_db
from app.schemas.memory import MemoryAnswer, Platform
from app.services.clarification import (
    check_and_strip_bot_mention,
    needs_clarification,
    strip_bot_mention,
)
from app.services.ingestion import ingest_messages
from app.services.response_router import handle_user_message

router = APIRouter()


def _normalize_phone(value: str | None) -> str:
    if not value:
        return ""
    return "".join(ch for ch in value if ch.isdigit())


@router.get("/whatsapp")
async def whatsapp_verify(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    if hub_mode == "subscribe" and hub_verify_token == settings.whatsapp_verify_token:
        return Response(content=hub_challenge or "", media_type="text/plain")
    return {"status": "ok", "provider": "wassenger" if settings.wassenger_api_key else "meta"}


@router.post("/whatsapp")
async def whatsapp_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Group behavior:
    1. Store EVERY inbound message into community memory
    2. If the message needs clarification, answer from previously shared chats
    3. Reply in the same group with answer + evidence trail
    """
    try:
        body_bytes = await request.body()
        if not body_bytes or not body_bytes.strip():
            return {"status": "ok", "message": "empty body"}
        payload = json.loads(body_bytes.decode("utf-8", errors="replace"))
    except Exception as exc:
        print(f"[WhatsApp Webhook] Invalid JSON payload: {exc}")
        return {"status": "ok", "error": "invalid json"}

    adapter: WhatsAppAdapter = get_adapter(Platform.WHATSAPP)  # type: ignore[assignment]

    event = payload.get("event")
    print(f"[WhatsApp Webhook Received] event={event}, keys={list(payload.keys())}")

    if event and event != "message:in:new" and "entry" not in payload:
        return {"status": "ignored", "event": event}

    messages = adapter.normalize_inbound(payload)
    if not messages:
        return {"status": "ignored"}

    # Never process our own outbound number as a user ask
    bot_phone = _normalize_phone(settings.wassenger_phone)

    stored = await ingest_messages(db, messages)
    stored_by_external = {m.external_id: m for m in stored}

    replies = 0
    skipped = 0
    delivery_errors = []

    for msg in messages:
        author_digits = _normalize_phone(msg.author_id)
        if bot_phone and author_digits and author_digits == bot_phone:
            skipped += 1
            continue

        is_group = bool((msg.metadata or {}).get("is_group"))
        text, was_mentioned = check_and_strip_bot_mention(
            msg.text,
            bot_names=["Unipod", "UniPods", "Memory", "JOTDS", "bot", "joe", "Joe"],
        )
        if not needs_clarification(text, is_group=is_group, was_mentioned=was_mentioned):
            skipped += 1
            continue

        exclude_ids = set()
        row = stored_by_external.get(msg.external_id)
        if row:
            exclude_ids.add(row.id)

        result, formatted = await handle_user_message(
            db,
            text=text,
            user_id=msg.author_id,
            user_name=msg.author_name,
            platform=Platform.WHATSAPP,
            conversation_id=msg.conversation_id if is_group else None,
            exclude_message_ids=exclude_ids,
            require_evidence=True,
        )

        # In groups: only reply when shared-chat evidence exists, OR if the bot was explicitly @mentioned
        if is_group and isinstance(result, MemoryAnswer):
            if (result.confidence == "insufficient" or not result.evidence) and not was_mentioned:
                skipped += 1
                continue

        try:
            delivery = await adapter.send_reply(
                conversation_id=msg.conversation_id,
                text=formatted,
                reply_to_id=msg.external_id,
                metadata=msg.metadata,
            )
            if not delivery.get("ok"):
                delivery_errors.append(delivery)
            else:
                replies += 1
        except Exception as exc:
            print(f"[WhatsApp] reply error: {exc}")
            delivery_errors.append({"ok": False, "error": str(exc)})

    return {
        "status": "ok",
        "provider": adapter.provider,
        "processed": len(messages),
        "ingested": len(stored),
        "replies": replies,
        "skipped": skipped,
        "delivery_errors": delivery_errors,
    }


@router.post("/teams")
async def teams_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    payload = await request.json()
    if payload.get("type") != "message":
        return {"status": "ignored", "type": payload.get("type")}

    adapter: TeamsAdapter = get_adapter(Platform.TEAMS)  # type: ignore[assignment]
    messages = adapter.normalize_inbound(payload)
    if not messages:
        return {"status": "ignored"}

    await ingest_messages(db, messages)
    replies = 0
    for msg in messages:
        if not needs_clarification(msg.text, is_group=False):
            continue
        _result, formatted = await handle_user_message(
            db,
            text=msg.text,
            user_id=msg.author_id,
            user_name=msg.author_name,
            platform=Platform.TEAMS,
            require_evidence=True,
        )
        await adapter.send_reply(
            conversation_id=msg.conversation_id,
            text=formatted,
            reply_to_id=msg.external_id,
            metadata=msg.metadata,
        )
        replies += 1
    return {"status": "ok", "processed": len(messages), "replies": replies}
