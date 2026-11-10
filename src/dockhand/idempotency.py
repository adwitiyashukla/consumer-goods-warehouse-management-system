from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any

from fastapi import Header, HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from dockhand.db import now
from dockhand.models import IdempotencyKey

Key = Annotated[str | None, Header(alias="Idempotency-Key", max_length=80)]


def digest(payload: Any) -> str:
    raw = json.dumps(jsonable_encoder(payload), sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


async def replay(session: AsyncSession, key: str | None, payload: Any) -> dict[str, Any] | None:
    if key is None:
        return None
    await session.execute(text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key})
    row = await session.get(IdempotencyKey, key)
    if row is None:
        return None
    if row.request_hash != digest(payload):
        raise HTTPException(422, "this idempotency key was already used for a different request")
    return row.body


async def remember(session: AsyncSession, key: str | None, payload: Any, body: dict[str, Any]) -> None:
    if key is not None:
        session.add(
            IdempotencyKey(
                key=key, request_hash=digest(payload), body=jsonable_encoder(body), created_at=now()
            )
        )
