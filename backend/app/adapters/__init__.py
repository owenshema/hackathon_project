from app.adapters.teams import TeamsAdapter
from app.adapters.whatsapp import WhatsAppAdapter
from app.schemas.memory import Platform

_adapters = {
    Platform.WHATSAPP: WhatsAppAdapter(),
    Platform.TEAMS: TeamsAdapter(),
}


def get_adapter(platform: Platform) -> WhatsAppAdapter | TeamsAdapter:
    adapter = _adapters.get(platform)
    if adapter is None:
        raise ValueError(f"No adapter for platform: {platform}")
    return adapter
