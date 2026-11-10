from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from dockhand import inbound, outbound, reports, tasks, yard
from dockhand.config import Settings, load_settings
from dockhand.db import make_engine, make_sessions
from dockhand.deps import SessionsDep
from dockhand.layout import Layout

Warmup = Callable[[], Awaitable[None]]


def create_app(settings: Settings | None = None, warmup: Warmup | None = None) -> FastAPI:
    settings = settings or load_settings()
    engine = make_engine(settings.database)

    async def warm(app: FastAPI, job: Warmup) -> None:
        try:
            await job()
        except Exception:
            logging.getLogger("dockhand").exception("the demo day did not load")
        finally:
            app.state.warming = False

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(warm(app, warmup)) if warmup is not None else None
        yield
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        await engine.dispose()

    app = FastAPI(title="dockhand", version="0.1.0", lifespan=lifespan)
    app.state.warming = warmup is not None
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessions = make_sessions(engine)
    app.state.layout = Layout(settings.layout)
    for module in (inbound, outbound, tasks, yard, reports):
        app.include_router(module.router)

    @app.get("/api/health/live", tags=["health"])
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/health/ready", tags=["health"])
    async def ready(request: Request, sessions: SessionsDep) -> dict[str, str]:
        if request.app.state.warming:
            raise HTTPException(503, "still loading the demo day")
        try:
            async with sessions() as session:
                await session.execute(text("select 1"))
        except Exception as error:
            raise HTTPException(503, "database is not reachable") from error
        return {"status": "ready"}

    static = Path(settings.server.static_dir)
    if static.is_dir():
        app.mount("/", StaticFiles(directory=static, html=True), name="web")
    return app
