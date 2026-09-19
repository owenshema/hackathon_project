from abc import ABC, abstractmethod
from typing import Any

from app.schemas.memory import NormalizedMessage, Platform


class PlatformAdapter(ABC):
    platform: Platform

    @property
    def is_configured(self) -> bool:
        return False

    @abstractmethod
    def normalize_inbound(self, payload: dict[str, Any]) -> list[NormalizedMessage]:
        """Convert a platform webhook payload into normalized messages."""

    @abstractmethod
    async def send_reply(
        self,
        *,
        conversation_id: str,
        text: str,
        reply_to_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Deliver a response back to the originating platform."""
