import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from wms.app import create_app
from wms.config import Settings, async_url, load_settings
from wms.db import make_engine, migrate, wipe
from wms.generator import Plan, seed
from wms.layout import Layout

ROOT = Path(__file__).resolve().parents[1]

SMALL: dict[str, dict[str, Any]] = {
    "layout": {"aisles": 6, "bays_per_aisle": 8, "levels": 3, "inbound_doors": 2, "outbound_doors": 2},
    "catalog": {"skus": 40},
    "customers": {"count": 6, "lanes": 2},
    "schedule": {
        "departures": ["09:00", "13:00"],
        "inbound_trucks": 3,
        "inbound_first": "04:00",
        "inbound_last": "12:00",
        "pallets_per_truck": 6,
    },
    "sim": {
        "orders": 16,
        "lines_per_order": [3, 8],
        "pickers": 2,
        "drivers": 2,
        "count_tasks": 4,
        "start": "03:00",
        "end": "15:00",
    },
}


@pytest.fixture(scope="session")
def repo_settings() -> Settings:
    return load_settings(ROOT / "config.toml")


@pytest.fixture(scope="session")
def database_url() -> str:
    raw = os.environ.get("TEST_DATABASE_URL")
    if not raw:
        pytest.skip("set TEST_DATABASE_URL to run the database tests")
    base = make_url(async_url(raw))
    target = base.set(database=f"{base.database}_test")

    async def recreate() -> None:
        engine = create_async_engine(base, isolation_level="AUTOCOMMIT")
        async with engine.connect() as conn:
            await conn.execute(text(f'drop database if exists "{target.database}" with (force)'))
            await conn.execute(text(f'create database "{target.database}"'))
        await engine.dispose()

    asyncio.run(recreate())
    url = target.render_as_string(hide_password=False)
    migrate(url)
    return url


@pytest.fixture(scope="session")
def settings(repo_settings: Settings, database_url: str) -> Settings:
    return repo_settings.with_overrides({**SMALL, "database": {"url": database_url, "pool_size": 5}})


@pytest.fixture
def layout(settings: Settings) -> Layout:
    return Layout(settings.layout)


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[FastAPI]:
    engine = make_engine(settings.database, pooled=False)
    await wipe(engine)
    await engine.dispose()
    application = create_app(settings)
    yield application
    await application.state.engine.dispose()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


@pytest.fixture
async def plan(app: FastAPI, settings: Settings) -> Plan:
    async with app.state.sessions() as session, session.begin():
        return await seed(session, settings)
