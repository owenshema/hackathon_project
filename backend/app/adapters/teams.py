"""Microsoft Teams Bot Framework adapter."""

from datetime import datetime, timezone
from typing import Any

import httpx

from app.adapters.base import PlatformAdapter
from app.core.config import settings
from app.schemas.memory import NormalizedMessage, Platform, SourceType


class TeamsAdapter(PlatformAdapter):
    platform = Platform.TEAMS
    _token: str | None = None

    @property
    def is_configured(self) -> bool:
        return bool(settings.teams_app_id and settings.teams_app_password)

    def normalize_inbound(self, payload: dict[str, Any]) -> list[NormalizedMessage]:
        """Normalize a Bot Framework activity."""
        activity_type = payload.get("type")
        if activity_type != "message":
            return []

        # Strip Teams @mentions markup like <at>Bot</at>
        raw = payload.get("text") or ""
        text = raw.replace("<at>", "").replace("</at>", "").strip()
        if not text:
            return []

        from_user = payload.get("from", {})
        conversation = payload.get("conversation", {})
        ts = payload.get("timestamp") or datetime.now(timezone.utc).isoformat()

        return [
            NormalizedMessage(
                platform=Platform.TEAMS,
                source_type=SourceType.CHAT,
                external_id=payload.get("id", ""),
                conversation_id=conversation.get("id", "teams"),
                author_id=from_user.get("id", "unknown"),
                author_name=from_user.get("name"),
                text=text,
                timestamp=ts,
                metadata={
                    "service_url": payload.get("serviceUrl"),
                    "channel_id": payload.get("channelId"),
                    "activity": payload,
                    "recipient_id": (payload.get("recipient") or {}).get("id"),
                },
            )
        ]

    async def _get_token(self) -> str:
        if self._token:
            return self._token
        data = {
            "grant_type": "client_credentials",
            "client_id": settings.teams_app_id,
            "client_secret": settings.teams_app_password,
            "scope": "https://api.botframework.com/.default",
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://login.microsoftonline.com/botframework.com/oauth2/v2.0/token",
                data=data,
            )
            resp.raise_for_status()
            self._token = resp.json()["access_token"]
            return self._token

    async def send_reply(
        self,
        *,
        conversation_id: str,
        text: str,
        reply_to_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        meta = metadata or {}
        service_url = (meta.get("service_url") or "").rstrip("/")
        activity = meta.get("activity") or {}

        if not self.is_configured or not service_url:
            print(f"[Teams mock reply → {conversation_id}] {text[:240]}")
            return {"ok": True, "mode": "mock", "conversation_id": conversation_id}

        token = await self._get_token()
        url = f"{service_url}/v3/conversations/{conversation_id}/activities"
        body: dict[str, Any] = {
            "type": "message",
            "text": text[:4000],
        }
        if reply_to_id:
            body["replyToId"] = reply_to_id
        # Preserve conversation reference when available
        if activity.get("conversation"):
            body["conversation"] = activity["conversation"]
        if activity.get("from"):
            body["recipient"] = activity["from"]
        if activity.get("recipient"):
            body["from"] = activity["recipient"]

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            if resp.status_code >= 400:
                print(f"[Teams] send failed {resp.status_code}: {resp.text[:300]}")
                # Clear cached token on auth failure
                if resp.status_code in {401, 403}:
                    self._token = None
                resp.raise_for_status()
            return {"ok": True, "mode": "live", "status": resp.status_code}
