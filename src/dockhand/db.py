from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import Executable, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from dockhand.config import DatabaseConfig
from dockhand.models import Base, Event

Sessions = async_sessionmaker[AsyncSession]


def make_engine(cfg: DatabaseConfig, pooled: bool = True) -> AsyncEngine:
    if not pooled:
        return create_async_engine(cfg.url, poolclass=NullPool, echo=cfg.echo)
    return create_async_engine(
        cfg.url,
        pool_size=cfg.pool_size,
        max_overflow=cfg.pool_size,
        pool_pre_ping=True,
        echo=cfg.echo,
    )


def make_sessions(engine: AsyncEngine) -> Sessions:
    return async_sessionmaker(engine, expire_on_commit=False)


def now() -> datetime:
    return datetime.now(UTC)


async def pairs(session: AsyncSession, query: Executable) -> dict[Any, Any]:
    return {row[0]: row[1] for row in await session.execute(query)}


def log(session: AsyncSession, at: datetime, kind: str, ref: str, **data: Any) -> None:
    session.add(Event(at=at, kind=kind, ref=ref, data=data))


def alembic_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", "dockhand:migrations")
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def migrate(url: str) -> None:
    command.upgrade(alembic_config(url), "head")


async def wipe(engine: AsyncEngine) -> None:
    names = ", ".join(table.name for table in reversed(Base.metadata.sorted_tables))
    async with engine.begin() as conn:
        await conn.execute(text(f"truncate {names} restart identity cascade"))
