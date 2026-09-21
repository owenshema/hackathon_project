"""Platform connection status + WhatsApp/Teams simulation."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters import get_adapter
from app.adapters.whatsapp import WhatsAppAdapter
from app.core.config import settings
from app.db.session import get_db
from app.schemas.memory import NormalizedMessage, Platform, SourceType
from app.services.ingestion import ingest_messages
from app.services.response_router import handle_user_message

router = APIRouter(prefix="/platforms")


@router.get("/status")
async def platforms_status(request: Request):
    base = str(request.base_url).rstrip("/")
    wa: WhatsAppAdapter = get_adapter(Platform.WHATSAPP)  # type: ignore[assignment]
    teams = get_adapter(Platform.TEAMS)

    wa_missing: list[str] = []
    if not settings.wassenger_api_key and not (
        settings.whatsapp_access_token and settings.whatsapp_phone_number_id
    ):
        wa_missing = ["WASSENGER_API_KEY (or Meta WHATSAPP_ACCESS_TOKEN + PHONE_NUMBER_ID)"]

    return {
        "whatsapp": {
            "configured": wa.is_configured,
            "provider": wa.provider,
            "phone": settings.wassenger_phone or None,
            "device_id": settings.wassenger_device_id or None,
            "webhook_url": f"{base}/api/v1/webhooks/whatsapp",
            "verify_token": settings.whatsapp_verify_token,
            "missing": wa_missing,
            "setup": [
                "Wassenger key is stored in WASSENGER_API_KEY",
                "Run: ngrok http 8000",
                "In Wassenger console → Webhooks → add URL:",
                f"  {base}/api/v1/webhooks/whatsapp  (use your ngrok https URL)",
                "Subscribe to event: message:in:new",
                "Message the connected WhatsApp number and ask a question",
            ],
        },
        "teams": {
            "configured": teams.is_configured,
            "webhook_url": f"{base}/api/v1/webhooks/teams",
            "missing": [
                name
                for name, ok in [
                    ("TEAMS_APP_ID", bool(settings.teams_app_id)),
                    ("TEAMS_APP_PASSWORD", bool(settings.teams_app_password)),
                ]
                if not ok
            ],
            "setup": [
                "Create Azure Bot (F0 free tier)",
                "Set messaging endpoint to https://<ngrok>/api/v1/webhooks/teams",
                "Paste App ID + client secret into TEAMS_APP_ID / TEAMS_APP_PASSWORD",
                "Install the bot in a Teams chat or channel",
            ],
        },
        "note": (
            "WhatsApp uses Wassenger. Expose the API with ngrok and register the webhook "
            "in the Wassenger dashboard for live replies."
        ),
    }


class SimulateRequest(BaseModel):
    platform: Platform = Platform.WHATSAPP
    text: str
    author_name: str = "Demo User"
    author_id: str = "demo-user"
    conversation_id: str = "demo-conversation"
    send_live: bool = False


@router.post("/simulate")
async def simulate_platform(body: SimulateRequest, db: AsyncSession = Depends(get_db)):
    if body.platform not in {Platform.WHATSAPP, Platform.TEAMS}:
        raise HTTPException(400, "platform must be whatsapp or teams")

    reply_kind = (
        "group"
        if body.platform == Platform.WHATSAPP
        and str(body.conversation_id).endswith("@g.us")
        else "phone"
    )
    nm = NormalizedMessage(
        platform=body.platform,
        source_type=SourceType.CHAT,
        external_id=f"sim-{datetime.now(timezone.utc).timestamp()}",
        conversation_id=body.conversation_id,
        author_id=body.author_id,
        author_name=body.author_name,
        text=body.text,
        timestamp=datetime.now(timezone.utc).isoformat(),
        metadata={
            "simulated": True,
            "reply_kind": reply_kind,
            "reply_to": body.conversation_id,
        },
    )
    await ingest_messages(db, [nm])
    result, formatted = await handle_user_message(
        db,
        text=body.text,
        user_id=body.author_id,
        user_name=body.author_name,
        platform=body.platform,
    )
    adapter = get_adapter(body.platform)
    delivery = {"ok": True, "mode": "skipped"}
    if body.send_live:
        delivery = await adapter.send_reply(
            conversation_id=body.conversation_id,
            text=formatted,
            reply_to_id=nm.external_id,
            metadata=nm.metadata,
        )
    return {
        "platform": body.platform.value,
        "configured": adapter.is_configured,
        "delivery": delivery,
        "formatted": formatted,
        "data": result.model_dump(mode="json"),
    }
