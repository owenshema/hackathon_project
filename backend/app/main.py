"""
UniPods Memory AI — FastAPI application entrypoint.

Architecture (see docs/ARCHITECTURE.md):
  Platform adapters → Ingestion → Raw store → Chunk/Embed → RAG → Response router
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import settings
from app.db.session import init_db, SessionLocal


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await init_db()

    # On every startup: pull the latest WhatsApp messages from Wassenger
    # and ingest any that are not yet in the DB, in chronological order.
    try:
        from app.services.sync import sync_recent_whatsapp_messages
        async with SessionLocal() as db:
            added = await sync_recent_whatsapp_messages(db, limit=200)
            if added:
                print(f"[Startup] Self-sync complete: {added} new messages added to memory.")
            else:
                print("[Startup] Self-sync complete: Memory is already up-to-date.")
    except Exception as exc:
        print(f"[Startup] Warning: startup self-sync failed (non-fatal): {exc}")

    try:
        from app.services.group_welcome import ensure_group_update_webhook
        await ensure_group_update_webhook()
    except Exception as exc:
        print(f"[Startup] Warning: could not update Wassenger webhook events: {exc}")

    yield


app = FastAPI(
    title="UniPods Memory AI",
    description=(
        "Community memory engine: WhatsApp + Teams + meetings → "
        "evidence-first answers with replayable citations."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    # Next.js may bind 3000/3001/3002 when ports are busy
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+" if settings.is_dev else None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/")
async def root():
    return {
        "name": "UniPods Memory AI",
        "tagline": "Ask what happened. Find what was decided. Know what you missed.",
        "docs": "/docs",
        "health": "/api/v1/health",
    }
