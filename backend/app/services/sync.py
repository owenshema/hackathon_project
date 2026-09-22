"""
Service to synchronize recent WhatsApp messages from Wassenger on startup and on-demand.

Fetches messages that occurred while the bot was offline or not yet ingested,
sorts them chronologically (oldest to newest), avoids duplicates, and embeds them
into PostgreSQL so that RAG memory and Catch Me Up are always up-to-date.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any
import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Message as MessageModel
from app.schemas.memory import NormalizedMessage, Platform, SourceType
from app.services.ingestion import ingest_messages
from app.services.extraction import extract_from_texts

WASSENGER_API = "https://api.wassenger.com/v1"

# Extended team roster mapping phone numbers to team member names
TEAM_ROSTER: dict[str, str] = {
    "+250782972679": "Shema Owen",
    "250782972679": "Shema Owen",
    "+250791690151": "Shema Owen",
    "250791690151": "Shema Owen",
    "+250789201681": "Joel",
    "250789201681": "Joel",
    "+26777177819": "Kgosi",
    "26777177819": "Kgosi",
    "+26658088018": "Reitumetse",
    "26658088018": "Reitumetse",
}


def _clean_phone(phone: str | None) -> str:
    if not phone:
        return ""
    return "".join(c for c in phone if c.isdigit())


def _resolve_author_name(raw_phone: str, contact_name: str | None = None) -> str:
    cleaned = _clean_phone(raw_phone)
    if cleaned in TEAM_ROSTER:
        return TEAM_ROSTER[cleaned]
    if raw_phone in TEAM_ROSTER:
        return TEAM_ROSTER[raw_phone]
    if contact_name and not contact_name.isdigit() and contact_name.lower() not in {"unknown", "null"}:
        return contact_name.strip()
    return f"+{cleaned}" if cleaned else "Team Member"


async def fetch_all_wassenger_chat_messages(
    device_id: str,
    api_key: str,
) -> list[dict[str, Any]]:
    """
    Paginate through ALL messages in Wassenger chat history (50 per page).
    Continues fetching until a page returns fewer than 50 results (last page).
    Returns every message collected, across all pages.
    """
    PAGE_SIZE = 50
    all_messages: list[dict[str, Any]] = []
    page = 0

    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            url = (
                f"{WASSENGER_API}/chat/{device_id}/messages"
                f"?size={PAGE_SIZE}&page={page}&sort=date:desc"
            )
            resp = await client.get(url, headers={"Token": api_key})
            if resp.status_code != 200:
                print(
                    f"[Sync] Wassenger fetch failed on page {page} "
                    f"({resp.status_code}): {resp.text[:300]}"
                )
                break

            data = resp.json()
            if isinstance(data, list):
                batch = data
            elif isinstance(data, dict):
                batch = data.get("data", [])
            else:
                break

            all_messages.extend(batch)
            print(f"[Sync] Fetched page {page}: {len(batch)} messages (total so far: {len(all_messages)})")

            # If fewer than PAGE_SIZE were returned, this was the last page
            if len(batch) < PAGE_SIZE:
                break

            page += 1

    return all_messages


async def sync_recent_whatsapp_messages(
    db: AsyncSession,
    *,
    limit: int = 100,  # kept for API compatibility but now ignored — all pages fetched
) -> int:
    """
    Fetch recent WhatsApp messages from Wassenger, filter already stored ones,
    order them strictly chronologically, and ingest + embed into PostgreSQL.
    """
    if not settings.wassenger_api_key or not settings.wassenger_device_id:
        print("[Sync] Wassenger not fully configured (missing API key or Device ID). Skipping sync.")
        return 0

    print(f"[Sync] Checking for recent WhatsApp messages to update database...")

    # Fetch all messages across all pages from Wassenger
    raw_messages = await fetch_all_wassenger_chat_messages(
        device_id=settings.wassenger_device_id,
        api_key=settings.wassenger_api_key,
    )
    if not raw_messages:
        print("[Sync] No messages returned from Wassenger API.")
        return 0

    # Extract all candidate external IDs
    bot_phone_digits = _clean_phone(settings.wassenger_phone)
    candidates_raw: list[dict[str, Any]] = []

    for item in raw_messages:
        flow = item.get("flow")
        if flow == "outbound":
            continue

        author_raw = str(item.get("author") or item.get("fromNumber") or item.get("from") or "")
        author_digits = _clean_phone(author_raw)
        if bot_phone_digits and author_digits and author_digits == bot_phone_digits:
            continue

        ext_id = str(item.get("id") or item.get("wid") or "")
        if not ext_id:
            continue

        candidates_raw.append(item)

    if not candidates_raw:
        print("[Sync] No new inbound candidate messages.")
        return 0

    candidate_ids = [str(c.get("id") or c.get("wid")) for c in candidates_raw]

    # Query DB for already stored external IDs
    existing_result = await db.execute(
        select(MessageModel.external_id).where(MessageModel.external_id.in_(candidate_ids))
    )
    already_stored = set(existing_result.scalars().all())

    to_ingest: list[NormalizedMessage] = []

    for item in candidates_raw:
        ext_id = str(item.get("id") or item.get("wid") or "")
        if ext_id in already_stored:
            continue

        # Extract text body or describe media
        body = (item.get("body") or item.get("message") or "").strip()
        msg_type = (item.get("type") or "text").lower()

        media = item.get("media") or {}
        media_url = media.get("url") or media.get("id")
        media_mime = media.get("mime") or media.get("mimetype")
        media_filename = media.get("filename") or ""

        # Include link preview info if available
        link_prev = item.get("linkPreview") or {}
        link_title = link_prev.get("title") or link_prev.get("description")

        if not body:
            if msg_type in {"audio", "ptt", "voice"}:
                body = "[voice message]"
            elif msg_type == "sticker":
                continue  # Skip plain stickers without text
            elif media_filename:
                body = f"[shared document: {media_filename}]"
            elif msg_type == "image":
                body = "[shared image]"
            else:
                continue

        if link_title and link_title != "Untitled document" and link_title not in body:
            body = f"{body} ({link_title})"

        # Authorship & conversation
        author_raw = str(item.get("author") or item.get("fromNumber") or item.get("from") or "")
        author_phone = _clean_phone(author_raw)
        contact_name = (
            (item.get("contact") or {}).get("displayName")
            or (item.get("fromContact") or {}).get("name")
            or item.get("authorName")
        )
        author_name = _resolve_author_name(author_phone, contact_name)

        chat_target = str(item.get("chat") or item.get("from") or "")
        is_group = bool(item.get("meta", {}).get("is_group")) or chat_target.endswith("@g.us")

        # Parse timestamp safely
        date_str = item.get("date") or item.get("createdAt")
        if not date_str and item.get("timestamp"):
            try:
                date_str = datetime.fromtimestamp(int(item["timestamp"]), tz=timezone.utc).isoformat()
            except Exception:
                date_str = datetime.now(timezone.utc).isoformat()
        if not date_str:
            date_str = datetime.now(timezone.utc).isoformat()

        nm = NormalizedMessage(
            platform=Platform.WHATSAPP,
            source_type=SourceType.VOICE if msg_type in {"audio", "ptt", "voice"} else SourceType.CHAT,
            external_id=ext_id,
            conversation_id=chat_target or "WhatsApp Group",
            author_id=f"+{author_phone}" if author_phone else author_raw,
            author_name=author_name,
            text=body,
            timestamp=date_str,
            media_url=str(media_url) if media_url else None,
            media_mime=str(media_mime) if media_mime else None,
            metadata={
                "provider": "wassenger",
                "is_group": is_group,
                "raw_type": msg_type,
                "synced_on_startup": True,
            },
        )
        to_ingest.append(nm)

    if not to_ingest:
        print("[Sync] Database is already fully up-to-date with all recent messages.")
        return 0

    # Sort strictly by timestamp in chronological order: oldest to newest
    to_ingest.sort(key=lambda m: m.timestamp)

    print(f"[Sync] Found {len(to_ingest)} new messages to ingest in chronological order.")
    for m in to_ingest:
        print(f"  -> [{m.timestamp}] {m.author_name}: {m.text[:70]}")

    stored_rows = await ingest_messages(db, to_ingest)

    # Automatically extract decisions and action items from the newly ingested batch
    try:
        texts = [f"{m.author_name}: {m.text}" for m in stored_rows if len(m.text) > 10]
        if texts:
            await extract_from_texts(db, texts=texts, evidence_messages=stored_rows)
            print("[Sync] Structured decisions & action items updated from new messages.")
    except Exception as exc:
        print(f"[Sync] Notice: could not run structured extraction on sync batch: {exc}")

    print(f"[Sync] Ingestion completed. Successfully added {len(stored_rows)} new messages to memory.")
    return len(stored_rows)
