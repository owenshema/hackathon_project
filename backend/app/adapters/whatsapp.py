"""WhatsApp adapter via Wassenger API (primary) with Meta Cloud API fallback."""

from datetime import datetime, timezone
import mimetypes
from pathlib import Path
from typing import Any

import httpx

from app.adapters.base import PlatformAdapter
from app.core.config import settings
from app.schemas.memory import NormalizedMessage, Platform, SourceType

WASSENGER_API = "https://api.wassenger.com/v1"


class WhatsAppAdapter(PlatformAdapter):
    platform = Platform.WHATSAPP

    @property
    def is_configured(self) -> bool:
        if settings.wassenger_api_key:
            return True
        return bool(
            settings.whatsapp_access_token and settings.whatsapp_phone_number_id
        )

    @property
    def provider(self) -> str:
        if settings.wassenger_api_key:
            return "wassenger"
        if settings.whatsapp_access_token and settings.whatsapp_phone_number_id:
            return "meta"
        return "none"

    def normalize_inbound(self, payload: dict[str, Any]) -> list[NormalizedMessage]:
        # Wassenger webhook shape
        if payload.get("event") or payload.get("object") == "message":
            return self._normalize_wassenger(payload)
        # Meta Cloud API shape
        return self._normalize_meta(payload)

    def _normalize_wassenger(self, payload: dict[str, Any]) -> list[NormalizedMessage]:
        event = payload.get("event") or ""
        if event and event != "message:in:new":
            return []

        data = payload.get("data") or payload
        if not isinstance(data, dict):
            return []

        # Ignore outbound echoes
        if data.get("flow") == "outbound":
            return []

        body = (data.get("body") or data.get("message") or "").strip()
        msg_type = (data.get("type") or "text").lower()
        source_type = SourceType.CHAT
        media_url = None
        media_mime = None

        if msg_type in {"audio", "ptt", "voice"}:
            source_type = SourceType.VOICE
            media = data.get("media") or {}
            media_url = media.get("url") or media.get("id")
            media_mime = media.get("mime") or media.get("mimetype")
            if not body:
                body = "[voice message — pending transcription]"
        elif msg_type not in {"text", "chat"} and not body:
            body = f"[{msg_type} message]"

        if not body:
            return []

        chat = data.get("chat") or {}
        contact = chat.get("contact") or data.get("contact") or {}
        author_phone = (
            data.get("fromNumber")
            or contact.get("phone")
            or data.get("from")
            or "unknown"
        )
        is_group = (chat.get("type") == "group") or str(
            data.get("from") or chat.get("id") or ""
        ).endswith("@g.us")
        group_id = None
        if is_group:
            group_id = chat.get("id") or data.get("chatId") or data.get("from")

        # Known team roster by phone number
        TEAM_ROSTER = {
            "+250782972679": "Shema Owen",
            "250782972679": "Shema Owen",
            "+250791690151": "Shema Owen",
            "250791690151": "Shema Owen",
            "+250789201681": "Joel",
            "250789201681": "Joel",
        }

        # For group messages, chat.name is the GROUP name (e.g. UNIPOD TASK GROUP).
        # We must never assign the group name as the author.
        author_name = (
            contact.get("displayName")
            or contact.get("name")
            or data.get("authorName")
            or data.get("participantName")
            or TEAM_ROSTER.get(author_phone)
            or (author_phone if is_group else chat.get("name"))
            or author_phone
        )
        if is_group and (author_name == chat.get("name") or not author_name):
            author_name = TEAM_ROSTER.get(author_phone) or author_phone

        conversation_id = group_id or author_phone
        # Reply target for Wassenger send API
        if is_group:
            reply_to = group_id
            reply_kind = "group"
        else:
            reply_to = author_phone
            reply_kind = "phone"

        ts = data.get("date") or datetime.now(timezone.utc).isoformat()
        if isinstance(data.get("timestamp"), (int, float)):
            try:
                ts = datetime.fromtimestamp(
                    int(data["timestamp"]), tz=timezone.utc
                ).isoformat()
            except (OSError, ValueError, OverflowError):
                pass

        return [
            NormalizedMessage(
                platform=Platform.WHATSAPP,
                source_type=source_type,
                external_id=str(data.get("id") or data.get("wid") or ""),
                conversation_id=str(conversation_id),
                author_id=str(author_phone),
                author_name=str(author_name),
                text=body,
                timestamp=ts if isinstance(ts, str) else datetime.now(timezone.utc).isoformat(),
                media_url=media_url,
                media_mime=media_mime,
                metadata={
                    "provider": "wassenger",
                    "raw_type": msg_type,
                    "is_group": is_group,
                    "reply_kind": reply_kind,
                    "reply_to": reply_to,
                    "device_id": (payload.get("device") or {}).get("id")
                    or settings.wassenger_device_id,
                },
            )
        ]

    def _normalize_meta(self, payload: dict[str, Any]) -> list[NormalizedMessage]:
        messages: list[NormalizedMessage] = []
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                contacts = {
                    c.get("wa_id"): c.get("profile", {}).get("name")
                    for c in value.get("contacts", [])
                }
                metadata = value.get("metadata", {})
                for msg in value.get("messages", []):
                    messages.append(
                        self._normalize_meta_one(msg, contacts, phone_meta=metadata)
                    )
        return messages

    def _normalize_meta_one(
        self,
        msg: dict[str, Any],
        contacts: dict[str, str | None],
        *,
        phone_meta: dict[str, Any] | None = None,
    ) -> NormalizedMessage:
        msg_type = msg.get("type", "text")
        text = ""
        source_type = SourceType.CHAT
        media_url = None
        media_mime = None

        if msg_type == "text":
            text = msg.get("text", {}).get("body", "")
        elif msg_type == "audio":
            source_type = SourceType.VOICE
            audio = msg.get("audio", {})
            media_url = audio.get("id")
            media_mime = audio.get("mime_type")
            text = "[voice message — pending transcription]"
        elif msg_type == "button":
            text = msg.get("button", {}).get("text", "")
        elif msg_type == "interactive":
            interactive = msg.get("interactive", {})
            text = (
                interactive.get("button_reply", {}).get("title")
                or interactive.get("list_reply", {}).get("title")
                or ""
            )
        else:
            text = f"[{msg_type} message]"

        ts = msg.get("timestamp")
        if ts:
            timestamp = datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
        else:
            timestamp = datetime.now(timezone.utc).isoformat()

        author_id = msg.get("from", "unknown")
        group_id = msg.get("group_id") or (msg.get("context") or {}).get("group_id")
        conversation_id = group_id or author_id

        return NormalizedMessage(
            platform=Platform.WHATSAPP,
            source_type=source_type,
            external_id=msg.get("id", ""),
            conversation_id=conversation_id,
            author_id=author_id,
            author_name=contacts.get(author_id),
            text=text,
            timestamp=timestamp,
            media_url=media_url,
            media_mime=media_mime,
            metadata={
                "provider": "meta",
                "raw_type": msg_type,
                "phone_number_id": (phone_meta or {}).get("phone_number_id"),
                "is_group": bool(group_id),
                "reply_to": author_id if group_id else conversation_id,
                "reply_kind": "phone",
            },
        )

    async def send_reply(
        self,
        *,
        conversation_id: str,
        text: str,
        reply_to_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        meta = metadata or {}
        to = meta.get("reply_to") or conversation_id
        kind = meta.get("reply_kind") or (
            "group" if str(to).endswith("@g.us") else "phone"
        )

        if settings.wassenger_api_key:
            return await self._send_wassenger(
                to=str(to), kind=str(kind), text=text, reply_to_id=reply_to_id
            )

        if settings.whatsapp_access_token and settings.whatsapp_phone_number_id:
            return await self._send_meta(to=str(to), text=text, reply_to_id=reply_to_id)

        print(f"[WhatsApp mock reply → {to}] {text[:240]}")
        return {"ok": True, "mode": "mock", "to": to}

    async def send_attachment(
        self,
        *,
        conversation_id: str,
        file_path: str,
        caption: str = "",
        display_filename: str | None = None,
        media_url: str | None = None,
        reply_to_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        meta = metadata or {}
        to = meta.get("reply_to") or conversation_id
        kind = meta.get("reply_kind") or (
            "group" if str(to).endswith("@g.us") else "phone"
        )

        if settings.wassenger_api_key:
            if media_url:
                return await self._send_wassenger_url_attachment(
                    to=str(to),
                    kind=str(kind),
                    media_url=media_url,
                    caption=caption,
                    display_filename=display_filename,
                    reply_to_id=reply_to_id,
                )
            return await self._send_wassenger_attachment(
                to=str(to),
                kind=str(kind),
                file_path=file_path,
                caption=caption,
                display_filename=display_filename,
                reply_to_id=reply_to_id,
            )

        print(f"[WhatsApp mock attachment -> {to}] {file_path}")
        return {"ok": True, "mode": "mock", "to": to, "file": file_path}

    async def _send_wassenger(
        self,
        *,
        to: str,
        kind: str,
        text: str,
        reply_to_id: str | None = None,
        media_file_id: str | None = None,
        media_url: str | None = None,
        display_filename: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"message": text[:4000]}
        if settings.wassenger_device_id:
            body["device"] = settings.wassenger_device_id
        if media_file_id:
            body["media"] = {"file": media_file_id}
        elif media_url:
            media: dict[str, Any] = {"url": media_url}
            if display_filename:
                media["filename"] = display_filename
            body["media"] = media

        if reply_to_id and not reply_to_id.startswith("sim-"):
            body["quote"] = reply_to_id

        if kind == "group" or to.endswith("@g.us"):
            body["group"] = to if "@" in to else f"{to}@g.us"
        else:
            phone = to if to.startswith("+") else f"+{to.lstrip('+')}"
            # Strip @c.us suffix if present
            phone = phone.split("@")[0]
            if not phone.startswith("+"):
                phone = f"+{phone}"
            body["phone"] = phone

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{WASSENGER_API}/messages",
                headers={
                    "Token": settings.wassenger_api_key,
                    "Content-Type": "application/json",
                },
                json=body,
            )
            if resp.status_code >= 400:
                detail = resp.text[:500]
                print(f"[Wassenger] send failed {resp.status_code}: {detail}")
                return {
                    "ok": False,
                    "mode": "wassenger",
                    "status": resp.status_code,
                    "error": detail,
                    "request": {k: v for k, v in body.items() if k != "message"},
                }
            return {"ok": True, "mode": "wassenger", "response": resp.json()}

    async def _send_wassenger_attachment(
        self,
        *,
        to: str,
        kind: str,
        file_path: str,
        caption: str,
        display_filename: str | None = None,
        reply_to_id: str | None = None,
    ) -> dict[str, Any]:
        path = Path(file_path)
        if not path.exists() or not path.is_file():
            return {
                "ok": False,
                "mode": "wassenger",
                "error": f"Attachment file not found: {file_path}",
            }

        upload_name = display_filename or path.name
        mime = mimetypes.guess_type(upload_name)[0] or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        async with httpx.AsyncClient(timeout=60) as client:
            with path.open("rb") as fh:
                upload = await client.post(
                    f"{WASSENGER_API}/files",
                    headers={"Token": settings.wassenger_api_key},
                    files={"file": (upload_name, fh, mime)},
                    data=(
                        {"device": settings.wassenger_device_id}
                        if settings.wassenger_device_id
                        else None
                    ),
                )
            if upload.status_code == 409:
                try:
                    data = upload.json()
                    file_id = (data.get("meta") or {}).get("file") or data.get("file")
                except ValueError:
                    file_id = None
                if not file_id:
                    detail = upload.text[:500]
                    print(f"[Wassenger] duplicate upload without file id: {detail}")
                    return {
                        "ok": False,
                        "mode": "wassenger",
                        "status": upload.status_code,
                        "error": detail,
                    }
                data = {"id": file_id}
            elif upload.status_code >= 400:
                detail = upload.text[:500]
                print(f"[Wassenger] file upload failed {upload.status_code}: {detail}")
                return {
                    "ok": False,
                    "mode": "wassenger",
                    "status": upload.status_code,
                    "error": detail,
                }

            else:
                data = upload.json()

            file_id = None
            if isinstance(data, list) and data:
                first = data[0] if isinstance(data[0], dict) else {}
                file_id = first.get("id") or first.get("_id") or first.get("file")
            elif isinstance(data, dict):
                file_id = (
                    data.get("id")
                    or data.get("_id")
                    or data.get("file")
                    or (data.get("data") or {}).get("id")
                )
            if not file_id:
                return {
                    "ok": False,
                    "mode": "wassenger",
                    "error": "Wassenger upload did not return a file id",
                    "response": data,
                }

        return await self._send_wassenger(
            to=to,
            kind=kind,
            text=caption or path.name,
            reply_to_id=reply_to_id,
            media_file_id=str(file_id),
        )

    async def _send_wassenger_url_attachment(
        self,
        *,
        to: str,
        kind: str,
        media_url: str,
        caption: str,
        display_filename: str | None = None,
        reply_to_id: str | None = None,
    ) -> dict[str, Any]:
        return await self._send_wassenger(
            to=to,
            kind=kind,
            text=caption,
            reply_to_id=reply_to_id,
            media_url=media_url,
            display_filename=display_filename,
        )

    async def _send_meta(
        self, *, to: str, text: str, reply_to_id: str | None
    ) -> dict[str, Any]:
        url = (
            f"https://graph.facebook.com/v21.0/"
            f"{settings.whatsapp_phone_number_id}/messages"
        )
        body: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"preview_url": False, "body": text[:4096]},
        }
        if reply_to_id:
            body["context"] = {"message_id": reply_to_id}

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {settings.whatsapp_access_token}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            if resp.status_code >= 400:
                print(f"[WhatsApp Meta] send failed {resp.status_code}: {resp.text[:300]}")
                resp.raise_for_status()
            return {"ok": True, "mode": "meta", "response": resp.json()}
