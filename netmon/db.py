"""Engine/session management. One process-wide async engine, created once at
startup from Settings.database and handed out via `session_scope()`.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from netmon.config import Settings
from netmon.models import Base

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def init_engine(settings: Settings) -> AsyncEngine:
    """Idempotent: safe to call once at startup. Tests call it again with a
    fresh in-memory settings object, which is fine -- it just repoints the
    module-level engine.
    """
    global _engine, _sessionmaker

    db_path = Path(settings.database.path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    _engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


async def create_all_tables() -> None:
    """Used by tests and by `scripts/create_admin.py` on a brand new install
    so there's a working schema even before `alembic upgrade head` is run.
    Real deployments should still run Alembic migrations for anything after
    the first install.
    """
    assert _engine is not None, "call init_engine() first"
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    assert _sessionmaker is not None, "call init_engine() first"
    async with _sessionmaker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
