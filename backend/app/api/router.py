from fastapi import APIRouter

from app.api.routes import catchup, chat, evidence, health, ingest, platforms, webhooks

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router, tags=["health"])
api_router.include_router(chat.router, tags=["chat"])
api_router.include_router(catchup.router, tags=["catchup"])
api_router.include_router(ingest.router, tags=["ingest"])
api_router.include_router(evidence.router, tags=["evidence"])
api_router.include_router(platforms.router, tags=["platforms"])
api_router.include_router(webhooks.router, prefix="/webhooks", tags=["webhooks"])
