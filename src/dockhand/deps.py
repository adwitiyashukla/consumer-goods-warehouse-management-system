from __future__ import annotations

from typing import Annotated, cast

from fastapi import Depends, Request

from dockhand.config import Settings
from dockhand.db import Sessions
from dockhand.layout import Layout


def get_sessions(request: Request) -> Sessions:
    return cast(Sessions, request.app.state.sessions)


def get_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_layout(request: Request) -> Layout:
    return cast(Layout, request.app.state.layout)


SessionsDep = Annotated[Sessions, Depends(get_sessions)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
LayoutDep = Annotated[Layout, Depends(get_layout)]
