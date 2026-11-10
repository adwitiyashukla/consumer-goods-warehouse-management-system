import asyncio

from alembic import context
from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from dockhand.models import Base

LOCK = 73101


def run(connection: Connection) -> None:
    connection.execute(text("select pg_advisory_lock(:key)"), {"key": LOCK})
    try:
        context.configure(connection=connection, target_metadata=Base.metadata)
        with context.begin_transaction():
            context.run_migrations()
        connection.commit()
    finally:
        connection.execute(text("select pg_advisory_unlock(:key)"), {"key": LOCK})
        connection.commit()


async def online() -> None:
    url = context.config.get_main_option("sqlalchemy.url")
    if url is None:
        raise RuntimeError("sqlalchemy.url is not set")
    engine = create_async_engine(url, poolclass=NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(run)
    await engine.dispose()


asyncio.run(online())
