from __future__ import annotations

from datetime import date, datetime
from typing import Any, ClassVar

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {datetime: DateTime(timezone=True), dict[str, Any]: JSONB}


class Location(Base):
    __tablename__ = "locations"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True)
    kind: Mapped[str] = mapped_column(String(10))
    aisle: Mapped[int | None]
    bay: Mapped[int | None]
    side: Mapped[str | None] = mapped_column(String(1))
    level: Mapped[int | None]
    x: Mapped[float]
    y: Mapped[float]


class Sku(Base):
    __tablename__ = "skus"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True)
    description: Mapped[str] = mapped_column(String(80))
    category: Mapped[str] = mapped_column(String(16))
    cases_per_pallet: Mapped[int]
    case_volume_m3: Mapped[float]
    case_weight_kg: Mapped[float]
    shelf_life_days: Mapped[int]
    velocity: Mapped[str | None] = mapped_column(String(1))
    pick_location_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id"), unique=True)


class Lot(Base):
    __tablename__ = "lots"
    __table_args__ = (UniqueConstraint("sku_id", "code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    sku_id: Mapped[int] = mapped_column(ForeignKey("skus.id"))
    code: Mapped[str] = mapped_column(String(24))
    produced_on: Mapped[date]
    expires_on: Mapped[date]


class Stock(Base):
    __tablename__ = "stock"
    __table_args__ = (
        CheckConstraint("cases_on_hand >= 0", name="on_hand_not_negative"),
        CheckConstraint(
            "cases_allocated >= 0 and cases_allocated <= cases_on_hand",
            name="allocated_within_on_hand",
        ),
        Index("ix_stock_sku_status", "sku_id", "status"),
        Index("ix_stock_location", "location_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    lpn: Mapped[str] = mapped_column(String(32), unique=True)
    sku_id: Mapped[int] = mapped_column(ForeignKey("skus.id"))
    lot_id: Mapped[int] = mapped_column(ForeignKey("lots.id"))
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"))
    cases_on_hand: Mapped[int]
    cases_allocated: Mapped[int] = mapped_column(default=0, server_default="0")
    status: Mapped[str] = mapped_column(String(10), default="available")


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True)
    name: Mapped[str] = mapped_column(String(60))
    lane: Mapped[int]
    min_shelf_life_days: Mapped[int]


class Appointment(Base):
    __tablename__ = "appointments"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(24), unique=True)
    direction: Mapped[str] = mapped_column(String(8))
    carrier: Mapped[str] = mapped_column(String(40))
    lane: Mapped[int | None]
    window_start: Mapped[datetime]
    window_end: Mapped[datetime]
    duration_minutes: Mapped[float]
    door_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id"))
    scheduled_start: Mapped[datetime | None]
    status: Mapped[str] = mapped_column(String(10), default="planned")
    gate_in_at: Mapped[datetime | None]
    docked_at: Mapped[datetime | None]
    done_at: Mapped[datetime | None]
    gate_out_at: Mapped[datetime | None]


class Asn(Base):
    __tablename__ = "asns"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(24), unique=True)
    origin: Mapped[str] = mapped_column(String(40))
    appointment_id: Mapped[int | None] = mapped_column(ForeignKey("appointments.id"))
    status: Mapped[str] = mapped_column(String(10), default="expected")
    received_at: Mapped[datetime | None]


class AsnLine(Base):
    __tablename__ = "asn_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    asn_id: Mapped[int] = mapped_column(ForeignKey("asns.id"), index=True)
    sku_id: Mapped[int] = mapped_column(ForeignKey("skus.id"))
    lot_code: Mapped[str] = mapped_column(String(24))
    produced_on: Mapped[date]
    expires_on: Mapped[date]
    cases_expected: Mapped[int]
    cases_received: Mapped[int] = mapped_column(default=0, server_default="0")


class Wave(Base):
    __tablename__ = "waves"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(24), unique=True)
    appointment_id: Mapped[int | None] = mapped_column(ForeignKey("appointments.id"))
    status: Mapped[str] = mapped_column(String(10), default="planned")
    routing: Mapped[str] = mapped_column(String(12))
    batching: Mapped[str] = mapped_column(String(8))
    planned_at: Mapped[datetime]
    released_at: Mapped[datetime | None]


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        Index("ix_orders_status", "status"),
        Index("ix_orders_appointment", "appointment_id"),
        Index("ix_orders_wave", "wave_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(24), unique=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    appointment_id: Mapped[int | None] = mapped_column(ForeignKey("appointments.id"))
    wave_id: Mapped[int | None] = mapped_column(ForeignKey("waves.id"))
    ship_by: Mapped[datetime]
    created_at: Mapped[datetime]
    status: Mapped[str] = mapped_column(String(10), default="open")
    picked_at: Mapped[datetime | None]
    shipped_at: Mapped[datetime | None]
    on_time: Mapped[bool | None]
    in_full: Mapped[bool | None]


class OrderLine(Base):
    __tablename__ = "order_lines"
    __table_args__ = (
        CheckConstraint(
            "cases_shipped <= cases_picked and cases_picked <= cases_allocated"
            " and cases_allocated <= cases_ordered",
            name="line_quantities",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    line_no: Mapped[int]
    sku_id: Mapped[int] = mapped_column(ForeignKey("skus.id"))
    cases_ordered: Mapped[int]
    cases_allocated: Mapped[int] = mapped_column(default=0, server_default="0")
    cases_picked: Mapped[int] = mapped_column(default=0, server_default="0")
    cases_shipped: Mapped[int] = mapped_column(default=0, server_default="0")


class Allocation(Base):
    __tablename__ = "allocations"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_line_id: Mapped[int] = mapped_column(ForeignKey("order_lines.id"), index=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stock.id"), index=True)
    cases: Mapped[int]
    kind: Mapped[str] = mapped_column(String(6))
    status: Mapped[str] = mapped_column(String(10), default="open")


class Tour(Base):
    __tablename__ = "tours"
    __table_args__ = (Index("ix_tours_status", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    wave_id: Mapped[int] = mapped_column(ForeignKey("waves.id"), index=True)
    seq: Mapped[int]
    status: Mapped[str] = mapped_column(String(10))
    routing: Mapped[str] = mapped_column(String(12))
    blocked: Mapped[int] = mapped_column(default=0, server_default="0")
    worker: Mapped[str | None] = mapped_column(String(24))
    distance_m: Mapped[float]
    stops: Mapped[int]
    cases: Mapped[int]
    volume_m3: Mapped[float]
    started_at: Mapped[datetime | None]
    done_at: Mapped[datetime | None]


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_queue", "status", "priority", "id"),
        Index("ix_tasks_tour", "tour_id"),
        Index("ix_tasks_allocation", "allocation_id"),
        Index(
            "ux_tasks_open_replen",
            "stock_id",
            unique=True,
            postgresql_where=text("kind = 'replen' and status in ('open', 'active')"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(8), default="open")
    priority: Mapped[int]
    wave_id: Mapped[int | None] = mapped_column(ForeignKey("waves.id"))
    tour_id: Mapped[int | None] = mapped_column(ForeignKey("tours.id"))
    seq: Mapped[int | None]
    sku_id: Mapped[int | None] = mapped_column(ForeignKey("skus.id"))
    stock_id: Mapped[int | None] = mapped_column(ForeignKey("stock.id"))
    allocation_id: Mapped[int | None] = mapped_column(ForeignKey("allocations.id"))
    from_location_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id"))
    to_location_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id"))
    cases: Mapped[int] = mapped_column(default=0, server_default="0")
    counted: Mapped[int | None]
    worker: Mapped[str | None] = mapped_column(String(24))
    created_at: Mapped[datetime]
    started_at: Mapped[datetime | None]
    done_at: Mapped[datetime | None]


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(index=True)
    kind: Mapped[str] = mapped_column(String(32))
    ref: Mapped[str] = mapped_column(String(32))
    data: Mapped[dict[str, Any]] = mapped_column(default=dict)


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    body: Mapped[dict[str, Any]]
    created_at: Mapped[datetime]
