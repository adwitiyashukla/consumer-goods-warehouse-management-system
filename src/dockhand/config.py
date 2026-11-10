from __future__ import annotations

import os
import tomllib
from datetime import date, time
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.engine import make_url

Routing = Literal["sshape", "largest_gap", "optimal"]
Batching = Literal["fcfs", "savings"]
Slotting = Literal["velocity", "random"]

ROUTINGS: tuple[Routing, ...] = ("sshape", "largest_gap", "optimal")


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def ordered(pair: tuple[float, float], name: str) -> None:
    if pair[0] > pair[1]:
        raise ValueError(f"{name} must be written as [low, high]")


class DatabaseConfig(Section):
    url: str = Field(min_length=1)
    pool_size: int = Field(ge=1, le=100)
    echo: bool


class ServerConfig(Section):
    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)
    static_dir: str
    demo_until: time


class LayoutConfig(Section):
    aisles: int = Field(ge=1, le=60)
    bays_per_aisle: int = Field(ge=1, le=100)
    levels: int = Field(ge=2, le=8)
    bay_width_m: float = Field(gt=0, le=10)
    aisle_pitch_m: float = Field(gt=0, le=30)
    end_gap_m: float = Field(ge=0, le=20)
    dock_depth_m: float = Field(ge=0, le=100)
    depot_aisle: int = Field(ge=0)
    inbound_doors: int = Field(ge=1, le=50)
    outbound_doors: int = Field(ge=1, le=50)

    @model_validator(mode="after")
    def check(self) -> Self:
        if self.depot_aisle >= self.aisles:
            raise ValueError("depot_aisle must be one of the aisles")
        return self

    @property
    def aisle_length_m(self) -> float:
        return 2 * self.end_gap_m + self.bays_per_aisle * self.bay_width_m

    @property
    def pick_slots(self) -> int:
        return self.aisles * 2 * self.bays_per_aisle

    @property
    def reserve_slots(self) -> int:
        return self.pick_slots * (self.levels - 1)


class CatalogConfig(Section):
    skus: int = Field(ge=1)
    cases_per_pallet: tuple[int, int]
    shelf_life_days: tuple[int, int]
    popularity_skew: float = Field(ge=0, le=3)

    @model_validator(mode="after")
    def check(self) -> Self:
        ordered(self.cases_per_pallet, "cases_per_pallet")
        ordered(self.shelf_life_days, "shelf_life_days")
        if self.cases_per_pallet[0] < 1 or self.shelf_life_days[0] < 1:
            raise ValueError("cases_per_pallet and shelf_life_days must be positive")
        return self


class CustomersConfig(Section):
    count: int = Field(ge=1)
    lanes: int = Field(ge=1)
    min_shelf_life_days: tuple[int, int]

    @model_validator(mode="after")
    def check(self) -> Self:
        ordered(self.min_shelf_life_days, "min_shelf_life_days")
        if self.lanes > self.count:
            raise ValueError("lanes cannot be more than customers")
        if self.min_shelf_life_days[0] < 0:
            raise ValueError("min_shelf_life_days must not be negative")
        return self


class ScheduleConfig(Section):
    date: date
    departures: list[time] = Field(min_length=1)
    lane_stagger_minutes: int = Field(ge=0, le=240)
    wave_lead_minutes: int = Field(ge=1, le=720)
    trailer_arrive_minutes: int = Field(ge=1, le=720)
    load_cutoff_minutes: int = Field(ge=0, le=240)
    inbound_trucks: int = Field(ge=0)
    inbound_first: time
    inbound_last: time
    pallets_per_truck: int = Field(ge=1, le=60)
    unload_minutes_per_pallet: float = Field(gt=0)
    door_turn_minutes: float = Field(ge=0)

    @model_validator(mode="after")
    def check(self) -> Self:
        if any(a >= b for a, b in zip(self.departures, self.departures[1:], strict=False)):
            raise ValueError("departures must be in increasing order")
        if self.inbound_first > self.inbound_last:
            raise ValueError("inbound_first must not be after inbound_last")
        if self.load_cutoff_minutes >= self.wave_lead_minutes:
            raise ValueError("load_cutoff_minutes must be shorter than wave_lead_minutes")
        return self


class PickingConfig(Section):
    routing: Routing
    batching: Batching
    full_pallets: bool
    tour_capacity_m3: float = Field(gt=0)
    tour_max_orders: int = Field(ge=1, le=20)
    walk_speed_mps: float = Field(gt=0)
    seconds_per_stop: float = Field(ge=0)
    seconds_per_case: float = Field(ge=0)


class ForkliftConfig(Section):
    speed_mps: float = Field(gt=0)
    seconds_per_move: float = Field(ge=0)


class SlottingConfig(Section):
    policy: Slotting
    a_share: float = Field(gt=0, lt=1)
    b_share: float = Field(ge=0, lt=1)
    replen_trigger: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def check(self) -> Self:
        if self.a_share + self.b_share > 1:
            raise ValueError("a_share plus b_share must be at most 1")
        return self


class SimConfig(Section):
    seed: int
    start: time
    end: time
    pickers: int = Field(ge=1, le=200)
    drivers: int = Field(ge=1, le=100)
    orders: int = Field(ge=0)
    lines_per_order: tuple[int, int]
    cases_per_line: float = Field(gt=0)
    order_lead_hours: tuple[float, float]
    initial_days_of_stock: float = Field(gt=0)
    lot_age_days: tuple[int, int]
    count_tasks: int = Field(ge=0)
    count_error_rate: float = Field(ge=0, le=1)
    replen_every_minutes: int = Field(ge=1)
    poll_minutes: float = Field(gt=0)
    arrival_jitter_minutes: float = Field(ge=0)

    @model_validator(mode="after")
    def check(self) -> Self:
        ordered(self.lines_per_order, "lines_per_order")
        ordered(self.order_lead_hours, "order_lead_hours")
        ordered(self.lot_age_days, "lot_age_days")
        if self.lines_per_order[0] < 1 or self.order_lead_hours[0] < 0 or self.lot_age_days[0] < 0:
            raise ValueError("lines_per_order, order_lead_hours and lot_age_days must not be negative")
        if self.start >= self.end:
            raise ValueError("sim start must be before sim end")
        return self


class Settings(Section):
    database: DatabaseConfig
    server: ServerConfig
    layout: LayoutConfig
    catalog: CatalogConfig
    customers: CustomersConfig
    schedule: ScheduleConfig
    picking: PickingConfig
    forklift: ForkliftConfig
    slotting: SlottingConfig
    sim: SimConfig

    @model_validator(mode="after")
    def check(self) -> Self:
        if self.catalog.skus > self.layout.pick_slots:
            raise ValueError("every sku needs a pick slot, so skus must fit in the pick slots")
        if self.sim.lot_age_days[1] >= self.catalog.shelf_life_days[0]:
            raise ValueError("lot_age_days must stay below the shortest shelf life")
        if self.sim.order_lead_hours[0] * 60 <= self.schedule.wave_lead_minutes:
            raise ValueError("orders must arrive before their wave is released, raise order_lead_hours")
        last = self.schedule.departures[-1]
        stagger = self.schedule.lane_stagger_minutes * (self.customers.lanes - 1)
        end = self.sim.end.hour * 60 + self.sim.end.minute
        if last.hour * 60 + last.minute + stagger > end:
            raise ValueError("the sim must run past the last staggered departure")
        if self.schedule.inbound_trucks and self.schedule.inbound_last >= self.sim.end:
            raise ValueError("the sim must run past the last inbound truck")
        return self

    def with_overrides(self, changes: dict[str, dict[str, Any]]) -> Settings:
        data = self.model_dump()
        for section, values in changes.items():
            data[section] = {**data[section], **values}
        return Settings.model_validate(data)


def async_url(url: str) -> str:
    parsed = make_url(url)
    if parsed.drivername in ("postgres", "postgresql", "postgresql+psycopg", "postgresql+psycopg2"):
        parsed = parsed.set(drivername="postgresql+asyncpg")
    query = dict(parsed.query)
    if "sslmode" in query:
        query["ssl"] = query.pop("sslmode")
        parsed = parsed.set(query=query)
    return parsed.render_as_string(hide_password=False)


def load_settings(path: str | Path | None = None) -> Settings:
    source = Path(path or os.environ.get("DOCKHAND_CONFIG", "config.toml"))
    data = tomllib.loads(source.read_text(encoding="utf-8"))
    url = os.environ.get("DATABASE_URL")
    if url:
        data.setdefault("database", {})["url"] = url
    if "database" in data and "url" in data["database"]:
        data["database"]["url"] = async_url(data["database"]["url"])
    return Settings.model_validate(data)
