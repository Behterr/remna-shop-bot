"""Async SQLAlchemy engine and session factory lifecycle."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


class Database:
    """Encapsulates SQLAlchemy AsyncEngine and async_sessionmaker."""

    def __init__(self, database_url: str, echo: bool = False) -> None:
        self.database_url = database_url
        engine_kwargs: dict = {
            "echo": echo,
            "pool_pre_ping": True,
        }
        if not database_url.startswith("sqlite"):
            engine_kwargs["pool_size"] = 10
            engine_kwargs["max_overflow"] = 20

        self.engine: AsyncEngine = create_async_engine(database_url, **engine_kwargs)
        self.session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            bind=self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Yield an async session with rollback on unhandled exceptions."""
        async with self.session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    async def ping(self) -> bool:
        """Verify database connectivity."""
        async with self.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True

    async def close(self) -> None:
        """Dispose engine connection pool."""
        await self.engine.dispose()


_database: Database | None = None


def init_database(database_url: str, echo: bool = False) -> Database:
    """Initialize global database singleton."""
    global _database
    _database = Database(database_url=database_url, echo=echo)
    return _database


def get_database() -> Database:
    """Return initialized database singleton."""
    if _database is None:
        raise RuntimeError("Database has not been initialized. Call init_database() first.")
    return _database
