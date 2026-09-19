from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.models import Base

connect_args = {}
if settings.use_sqlite:
    # Ensure data directory exists for SQLite file
    db_path = settings.database_url.split("///")[-1]
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    connect_args = {"check_same_thread": False}

engine = create_async_engine(
    settings.database_url,
    echo=False,
    connect_args=connect_args,
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    """Create tables (and enable pgvector on Postgres when configured)."""
    try:
        async with engine.begin() as conn:
            if settings.use_pgvector and not settings.use_sqlite:
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)
        if settings.use_sqlite:
            mode = "sqlite"
        elif settings.use_pgvector:
            mode = "postgres+pgvector"
        else:
            mode = "postgres (JSON embeddings)"
        print(f"[db] connected ({mode}) and schema ready")
    except Exception as exc:
        print(f"[db] startup failed — {exc}")
