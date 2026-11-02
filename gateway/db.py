"""Async PostgreSQL engine and session factory."""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from gateway.config import get_settings


class Base(DeclarativeBase):
    pass


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is None:
        settings = get_settings()
        if settings.environment == "test":
            # Each test runs on its own event loop; pooled asyncpg connections can't cross loops.
            _engine = create_async_engine(settings.database_url, poolclass=NullPool)
        else:
            _engine = create_async_engine(
                settings.database_url, pool_pre_ping=True, pool_size=10, max_overflow=20
            )
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


async def get_session() -> AsyncIterator[AsyncSession]:
    get_engine()
    if _sessionmaker is None:  # pragma: no cover
        raise RuntimeError("Session factory not initialised")
    async with _sessionmaker() as session:
        yield session


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine, _sessionmaker = None, None
