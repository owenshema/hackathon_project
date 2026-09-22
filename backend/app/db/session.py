from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.models import Base

database_url = settings.database_url.strip().strip("\"'")
# Managed PostgreSQL providers commonly supply a standard ``postgresql://``
# URL, while SQLAlchemy's async engine needs an explicit async driver.
# Keep this normalization beside engine construction as a defensive fallback
# for process environments that bypass settings validation.
if database_url.startswith("postgresql://"):
    database_url = "postgresql+asyncpg://" + database_url.removeprefix("postgresql://")

connect_args = {}
if database_url.startswith("sqlite"):
    # Ensure data directory exists for SQLite file
    db_path = database_url.split("///")[-1]
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    connect_args = {"check_same_thread": False}

engine = create_async_engine(
    database_url,
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
