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
from app.db.session import init_db


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await init_db()
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
