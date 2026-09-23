import json
from pathlib import Path
from urllib.parse import quote
from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters import get_adapter
from app.adapters.teams import TeamsAdapter
from app.adapters.whatsapp import WhatsAppAdapter
from app.core.config import settings
from app.db.models import Message as MessageModel
from app.db.session import SessionLocal, get_db
from app.schemas.memory import MemoryAnswer, Platform
from app.services.clarification import (
    check_and_strip_bot_mention,
    is_addressed_to_other_bot,
    is_external_bot_author,
    is_informational_share,
    needs_clarification,
)
from app.core.group_profiles import is_group_admin
from app.services.voice_recap import (
    is_voice_recap_request,
    save_voice_answer,
    synthesize_voice_note,
)
from app.services.group_recap import maybe_send_group_recaps
from app.services.group_welcome import (
    bot_was_added_to_group,
    extract_group_id,
    maybe_send_group_welcome,
)
from app.services.ingestion import ingest_messages
from app.services.response_router import handle_user_message

router = APIRouter()

# In-memory guard against concurrent retries of the same message
_in_flight: set[str] = set()
# Avoid thanking the same sharer repeatedly in a short window
_share_thanks_cooldown: dict[str, float] = {}
_SHARE_THANKS_TTL_SECONDS = 20 * 60


def _normalize_phone(value: str | None) -> str:
    if not value:
        return ""
    return "".join(ch for ch in value if ch.isdigit())


def _share_author_mention(author_name: str | None) -> str:
    clean = (author_name or "").strip()
    if not clean:
        return "@someone"
    if clean.startswith("@"):
        return clean
    first = clean.split()[0].strip("():,~")
    known = {
        "diane": "@Diane",
        "gift": "@Gift",
        "munira": "@Munira",
        "jeovaire": "@Jeovaire",
        "charles": "@Charles",
    }
    return known.get(first.lower(), f"@{first}" if first else "@someone")


def _should_thank_share(conversation_id: str, author_id: str) -> bool:
    import time

    key = f"{conversation_id}:{author_id}"
    now = time.time()
    # Drop expired entries lightly
    expired = [k for k, ts in _share_thanks_cooldown.items() if now - ts > _SHARE_THANKS_TTL_SECONDS]
    for k in expired:
        _share_thanks_cooldown.pop(k, None)
    last = _share_thanks_cooldown.get(key)
    if last and now - last < _SHARE_THANKS_TTL_SECONDS:
        return False
    _share_thanks_cooldown[key] = now
    return True


ADMIN_MENTIONS = "@250782972679 (Shema) @250789201681 (Joel)"

OUR_BOT_NAMES = [
    "JOTDS bot",
    "JOTDS_bot",
    "JOTDS",
    "jotds",
    "The Palm",
    "Palm",
    "joe",
]


def _evidence_attachments(answer: MemoryAnswer) -> list[tuple[str, str, str, str | None]]:
    attachments: list[tuple[str, str, str, str | None]] = []
    seen: set[str] = set()
    allowed_suffixes = {
        ".pdf",
        ".ppt",
        ".pptx",
        ".doc",
        ".docx",
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
    }
    for ev in answer.evidence:
        if not ev.media_url:
            continue
        path = Path(ev.media_url)
        if not path.exists() or not path.is_file():
            continue
        if path.suffix.lower() not in allowed_suffixes:
            continue
        key = str(path.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        author = ev.author or "an official source"
        filename = ev.media_filename or path.name
        public_url = None
        if settings.public_base_url and ev.message_id:
            safe_filename = quote(filename)
            public_url = (
                f"{settings.public_base_url.rstrip('/')}"
                f"/api/v1/evidence/media/{ev.message_id}/{safe_filename}"
            )
        attachments.append(
            (
                str(path),
                filename,
                f"Source file shared by {author}: {filename}",
                public_url,
            )
        )
        if len(attachments) >= 2:
            break
    return attachments


def _format_admin_attention_reply(question: str, asker_name: str | None) -> str:
    asker = asker_name or "someone"
    clean_question = " ".join((question or "").split())
    if len(clean_question) > 240:
        clean_question = clean_question[:237] + "..."
    return (
        f"{ADMIN_MENTIONS} this looks important, but I couldn't find a clear answer "
        f"in the group memory yet.\n\n"
        f"{asker} asked: \"{clean_question}\"\n\n"
        "Please share the correct information so everyone can rely on it."
    )


@router.get("/whatsapp")
async def whatsapp_verify(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    if hub_mode == "subscribe" and hub_verify_token == settings.whatsapp_verify_token:
        return Response(content=hub_challenge or "", media_type="text/plain")
    return {"status": "ok", "provider": "wassenger" if settings.wassenger_api_key else "meta"}


async def _handle_whatsapp_payload(payload: dict) -> None:
    """
    Background task — runs AFTER 200 OK is already returned to Wassenger.
    This prevents Wassenger from retrying due to slow LLM response times.
    Two-layer deduplication:
      1. In-memory _in_flight set (prevents concurrent retries)
      2. DB external_id check (prevents retries after restart)
    """
    adapter: WhatsAppAdapter = get_adapter(Platform.WHATSAPP)  # type: ignore[assignment]
    bot_phone = _normalize_phone(settings.wassenger_phone)

    if bot_was_added_to_group(payload):
        group_id = extract_group_id(payload)
        if group_id:
            async with SessionLocal() as db:
                await maybe_send_group_welcome(
                    db, adapter, conversation_id=group_id, payload=payload
                )
        if str(payload.get("event") or "") == "group:update":
            return

    messages = adapter.normalize_inbound(payload)
    if not messages:
        return

    async with SessionLocal() as db:
        # Layer 1: filter out any IDs already in-flight
        candidates = [m for m in messages if m.external_id not in _in_flight]
        if not candidates:
            print("[WhatsApp] All messages already in-flight — skipping.")
            return

        # Layer 2: filter out any IDs already stored in DB (Wassenger retry guard)
        incoming_ids = [m.external_id for m in candidates if m.external_id]
        if incoming_ids:
            rows = await db.execute(
                select(MessageModel.external_id).where(
                    MessageModel.external_id.in_(incoming_ids)
                )
            )
            already_stored = set(rows.scalars().all())
            candidates = [m for m in candidates if m.external_id not in already_stored]

        if not candidates:
            print("[WhatsApp] All messages already stored (duplicate delivery) — skipping.")
            return

        # Mark as in-flight
        new_ids = {m.external_id for m in candidates}
        _in_flight.update(new_ids)

        try:
            stored = await ingest_messages(db, candidates)
            stored_by_external = {m.external_id: m for m in stored}

            await maybe_send_group_recaps(db, adapter, candidates)

            for msg in candidates:
                # Skip bot's own echoed outbound messages
                author_digits = _normalize_phone(msg.author_id)
                if bot_phone and author_digits and author_digits == bot_phone:
                    continue

                # Never reply to other bots in the group (names like "SPARK BOT").
                if is_external_bot_author(msg.author_name):
                    print(
                        f"[WhatsApp] Skipping reply to bot author: {msg.author_name!r}"
                    )
                    continue

                is_group = bool((msg.metadata or {}).get("is_group"))
                if is_group and bot_was_added_to_group(payload):
                    await maybe_send_group_welcome(
                        db,
                        adapter,
                        conversation_id=msg.conversation_id,
                        payload=payload,
                    )
                    continue

                text, was_mentioned = check_and_strip_bot_mention(
                    msg.text,
                    bot_names=OUR_BOT_NAMES,
                )

                # Someone tagged another team's bot (e.g. @Zak Bot) — stay silent.
                if is_group and is_addressed_to_other_bot(
                    msg.text, our_bot_names=OUR_BOT_NAMES
                ):
                    if not was_mentioned:
                        print(
                            f"[WhatsApp] Skipping — addressed to another bot: "
                            f"{(msg.text or '')[:80]!r}"
                        )
                        continue

                meta = msg.metadata or {}
                has_media = bool(
                    meta.get("has_media")
                    or meta.get("media_url")
                    or msg.media_url
                    or (msg.media_mime and not str(msg.media_mime).startswith("text/"))
                )

                # Appreciate admins/members who share useful info instead of inventing.
                if (
                    is_group
                    and not was_mentioned
                    and is_informational_share(
                        text, has_media=has_media, was_mentioned=False
                    )
                    and (
                        is_group_admin(
                            msg.conversation_id,
                            author_id=msg.author_id,
                            author_name=msg.author_name,
                        )
                        or has_media
                        or "http://" in text.lower()
                        or "https://" in text.lower()
                    )
                    and _should_thank_share(msg.conversation_id, msg.author_id)
                ):
                    mention = _share_author_mention(msg.author_name)
                    thanks = f"Thanks {mention} for sharing this 🙌"
                    try:
                        await adapter.send_reply(
                            conversation_id=msg.conversation_id,
                            text=thanks,
                            reply_to_id=msg.external_id,
                            metadata=msg.metadata,
                        )
                        print(f"[WhatsApp] Appreciated share from {msg.author_name!r}")
                    except Exception as exc:
                        print(f"[WhatsApp] Share thanks failed: {exc}")
                    # Don't also run RAG on a share unless they asked us something.
                    if not needs_clarification(
                        text, is_group=True, was_mentioned=False
                    ):
                        continue

                # In groups with several bots: only answer when we were @mentioned,
                # or for slash commands / clear voice-delivery requests.
                # Bare "?" questions meant for Zak Bot / others must stay silent.
                if is_group and not was_mentioned:
                    lower = text.lower().lstrip()
                    allow_untagged = lower.startswith("/") or is_voice_recap_request(text)
                    if not allow_untagged:
                        # Still appreciate shares above; otherwise do not rag-answer.
                        continue

                # Direct chats: answer meaningful questions without requiring a tag.
                if not is_group and not needs_clarification(
                    text, is_group=False, was_mentioned=True
                ):
                    continue

                exclude_ids: set = set()
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

                # In groups: stay silent unless we actually have an answer.
                # Important unanswered gaps can still be flagged to admins.
                if is_group and isinstance(result, MemoryAnswer):
                    if result.confidence == "insufficient":
                        if was_mentioned:
                            formatted = (
                                "I don't have that in this group's memory yet, "
                                "so I won't guess."
                            )
                        else:
                            continue

                # Keep every answer actually sent to this requester in this
                # chat. A later voice request reads that specific answer,
                # rather than falling back to a generic catch-up.
                if formatted.strip() and not (
                    isinstance(result, MemoryAnswer) and result.deliver_voice_recap
                ):
                    await save_voice_answer(
                        db,
                        msg.author_id,
                        msg.conversation_id if is_group else None,
                        formatted,
                    )

                try:
                    if (
                        isinstance(result, MemoryAnswer)
                        and result.deliver_voice_recap
                        and result.voice_recap_script
                    ):
                        audio_path = await synthesize_voice_note(result.voice_recap_script)
                        if audio_path:
                            await adapter.send_reply(
                                conversation_id=msg.conversation_id,
                                text="Sending voice message 🎙",
                                reply_to_id=msg.external_id,
                                metadata=msg.metadata,
                            )
                            voice_delivery = await adapter.send_attachment(
                                conversation_id=msg.conversation_id,
                                file_path=str(audio_path),
                                caption="",
                                display_filename="voice-message.mp3",
                                reply_to_id=msg.external_id,
                                metadata=msg.metadata,
                            )
                            print(
                                "[WhatsApp] Voice message "
                                f"ok={voice_delivery.get('ok')} mode={voice_delivery.get('mode')}"
                            )
                            if voice_delivery.get("ok"):
                                continue
                        await adapter.send_reply(
                            conversation_id=msg.conversation_id,
                            text=(
                                "I couldn't generate the voice note on this server. "
                                "Please try again in a moment."
                            ),
                            reply_to_id=msg.external_id,
                            metadata=msg.metadata,
                        )
                        continue

                    attachments = (
                        _evidence_attachments(result)
                        if isinstance(result, MemoryAnswer) and result.confidence != "insufficient"
                        else []
                    )
                    if attachments:
                        file_path, display_filename, _caption, public_url = attachments[0]
                        delivery = await adapter.send_attachment(
                            conversation_id=msg.conversation_id,
                            file_path=file_path,
                            caption=formatted,
                            display_filename=display_filename,
                            media_url=public_url,
                            reply_to_id=msg.external_id,
                            metadata=msg.metadata,
                        )
                        print(
                            "[WhatsApp] Reply attachment "
                            f"ok={delivery.get('ok')} mode={delivery.get('mode')}"
                        )
                        if not delivery.get("ok"):
                            delivery = await adapter.send_reply(
                                conversation_id=msg.conversation_id,
                                text=formatted,
                                reply_to_id=msg.external_id,
                                metadata=msg.metadata,
                            )
                            print(f"[WhatsApp] Reply ok={delivery.get('ok')} mode={delivery.get('mode')}")
                    else:
                        delivery = await adapter.send_reply(
                            conversation_id=msg.conversation_id,
                            text=formatted,
                            reply_to_id=msg.external_id,
                            metadata=msg.metadata,
                        )
                        print(f"[WhatsApp] Reply ok={delivery.get('ok')} mode={delivery.get('mode')}")

                except Exception as exc:
                    print(f"[WhatsApp] Reply error: {exc}")
        finally:
            # Always clear in-flight marks
            for eid in new_ids:
                _in_flight.discard(eid)


@router.post("/whatsapp")
async def whatsapp_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
):
    """
    Receives Wassenger inbound webhook events.
    Returns HTTP 200 IMMEDIATELY so Wassenger never times out and retries.
    All processing (deduplication, RAG, reply) is done in a background task.
    """
    try:
        body_bytes = await request.body()
        if not body_bytes or not body_bytes.strip():
            return {"status": "ok", "message": "empty body"}
        payload = json.loads(body_bytes.decode("utf-8", errors="replace"))
    except Exception as exc:
        print(f"[WhatsApp Webhook] Invalid JSON: {exc}")
        return {"status": "ok", "error": "invalid json"}

    event = payload.get("event")
    print(f"[WhatsApp Webhook] event={event}")

    allowed = {None, "", "message:in:new", "group:update"}
    if event and event not in allowed and "entry" not in payload:
        return {"status": "ignored", "event": event}

    # Guard against Wassenger retry floods from tunnel downtime.
    # Wassenger retries failed deliveries — when the tunnel was down it queues many retries.
    # retries > 2 means an old stale re-delivery — skip it to prevent duplicates.
    retry_count = int(payload.get("retries", 0) or 0)
    if retry_count > 2:
        print(f"[WhatsApp Webhook] Skipping stale retry (retries={retry_count})")
        return {"status": "ok", "message": "stale retry skipped"}

    # Schedule background processing — return 200 right away
    background_tasks.add_task(_handle_whatsapp_payload, payload)
    return {"status": "ok", "message": "queued"}


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
        if is_external_bot_author(msg.author_name):
            continue
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
