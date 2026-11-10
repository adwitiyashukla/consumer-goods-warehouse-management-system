from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class At(Body):
    at: AwareDatetime | None = None


class AsnLineIn(Body):
    sku: str
    lot: str = Field(min_length=1, max_length=24)
    produced_on: date
    expires_on: date
    cases: int = Field(gt=0)


class AsnIn(At):
    code: str = Field(min_length=1, max_length=24)
    origin: str = Field(min_length=1, max_length=40)
    appointment: str | None = None
    lines: list[AsnLineIn] = Field(min_length=1)


class ReceivedLine(Body):
    sku: str
    lot: str
    cases: int = Field(ge=0)


class ReceiveIn(At):
    lines: list[ReceivedLine] | None = None


class OrderLineIn(Body):
    sku: str
    cases: int = Field(gt=0)


class OrderIn(At):
    code: str = Field(min_length=1, max_length=24)
    customer: str
    ship_by: AwareDatetime
    appointment: str | None = None
    allocate: bool = True
    lines: list[OrderLineIn] = Field(min_length=1)


class WaveIn(At):
    appointment: str | None = None
    release: bool = False


class NextIn(At):
    worker: str = Field(min_length=1, max_length=24)
    role: Literal["picker", "driver"]


class CompleteIn(At):
    worker: str | None = Field(default=None, max_length=24)
    cases: int | None = Field(default=None, ge=0)
    counted: int | None = Field(default=None, ge=0)


class CountIn(At):
    count: int = Field(gt=0, le=1000)
    seed: int = 0


class AppointmentIn(Body):
    code: str = Field(min_length=1, max_length=24)
    direction: Literal["inbound", "outbound"]
    carrier: str = Field(min_length=1, max_length=40)
    lane: int | None = None
    window_start: AwareDatetime
    window_end: AwareDatetime
    duration_minutes: float = Field(gt=0)


class DockIn(At):
    door: str | None = None


def stamp(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
