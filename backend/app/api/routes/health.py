from fastapi import APIRouter

from app.core.config import settings
from app.adapters import get_adapter
from app.schemas.memory import Platform

router = APIRouter()


@router.get("/health")
async def health():
    wa = get_adapter(Platform.WHATSAPP)
    teams = get_adapter(Platform.TEAMS)
    return {
        "status": "ok",
        "env": settings.app_env,
        "llm": settings.active_llm,
        "whatsapp_configured": wa.is_configured,
        "teams_configured": teams.is_configured,
        "platforms": "/api/v1/platforms/status",
    }
