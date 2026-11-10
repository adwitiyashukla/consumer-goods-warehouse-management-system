from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dockhand.db import pairs
from dockhand.deps import LayoutDep, SessionsDep
from dockhand.models import (
    Appointment,
    Customer,
    Event,
    Location,
    Lot,
    Order,
    OrderLine,
    Sku,
    Stock,
    Task,
    Tour,
    Wave,
)
from dockhand.schemas import stamp

router = APIRouter(prefix="/api", tags=["reports"])


def ratio(part: int | None, whole: int | None) -> float | None:
    return round(part / whole, 4) if part is not None and whole else None


async def dock_stats(session: AsyncSession, direction: str, door_kind: str) -> dict[str, Any]:
    dwell = func.extract("epoch", Appointment.gate_out_at - Appointment.gate_in_at) / 60
    busy = func.extract("epoch", Appointment.done_at - Appointment.docked_at) / 60
    row = (
        await session.execute(
            select(
                func.count(Appointment.id).filter(Appointment.gate_out_at.is_not(None)),
                func.avg(dwell),
                func.percentile_cont(0.9).within_group(dwell),
                func.sum(busy),
                func.min(Appointment.docked_at),
                func.max(Appointment.done_at),
            ).where(Appointment.direction == direction)
        )
    ).one()
    doors = await session.scalar(select(func.count(Location.id)).where(Location.kind == door_kind))
    utilization = None
    if row[3] is not None and row[4] is not None and row[5] is not None and doors:
        span = (row[5] - row[4]).total_seconds() / 60
        utilization = round(float(row[3]) / (span * doors), 4) if span > 0 else None
    return {
        "trucks": row[0],
        "dwell_avg_min": round(float(row[1]), 1) if row[1] is not None else None,
        "dwell_p90_min": round(float(row[2]), 1) if row[2] is not None else None,
        "door_utilization": utilization,
    }


async def kpis(session: AsyncSession) -> dict[str, Any]:
    statuses = await pairs(session, select(Order.status, func.count(Order.id)).group_by(Order.status))
    shipped = (
        await session.execute(
            select(
                func.count(Order.id),
                func.count(Order.id).filter(and_(Order.on_time.is_(True), Order.in_full.is_(True))),
                func.count(Order.id).filter(Order.on_time.is_(True)),
                func.count(Order.id).filter(Order.in_full.is_(True)),
            ).where(Order.status == "shipped")
        )
    ).one()
    lines = (
        await session.execute(
            select(
                func.count(OrderLine.id),
                func.coalesce(func.sum(OrderLine.cases_ordered), 0),
                func.coalesce(func.sum(OrderLine.cases_allocated), 0),
                func.coalesce(func.sum(OrderLine.cases_picked), 0),
            )
        )
    ).one()
    done = Tour.status == "done"
    tours = (
        await session.execute(
            select(
                func.count(Tour.id).filter(done),
                func.coalesce(func.sum(Tour.distance_m).filter(done), 0.0),
                func.coalesce(func.sum(Tour.stops).filter(done), 0),
                func.coalesce(
                    func.sum(func.extract("epoch", Tour.done_at - Tour.started_at)).filter(done), 0
                ),
            )
        )
    ).one()
    open_tasks = await pairs(
        session,
        select(Task.kind, func.count(Task.id)).where(Task.status.in_(("open", "active"))).group_by(Task.kind),
    )
    stock = (
        await session.execute(
            select(
                func.count(Stock.id).filter(Stock.status != "empty"),
                func.coalesce(func.sum(Stock.cases_on_hand), 0),
                func.coalesce(func.sum(Stock.cases_allocated), 0),
                func.count(Stock.id).filter(Stock.cases_allocated > Stock.cases_on_hand),
            )
        )
    ).one()
    return {
        "orders": {"by_status": statuses, "total": sum(statuses.values())},
        "shipped": {
            "orders": shipped[0],
            "otif": ratio(shipped[1], shipped[0]),
            "on_time": ratio(shipped[2], shipped[0]),
            "in_full": ratio(shipped[3], shipped[0]),
        },
        "lines": {
            "lines": lines[0],
            "cases": int(lines[1]),
            "allocated": int(lines[2]),
            "picked": int(lines[3]),
            "short": int(lines[1]) - int(lines[2]),
        },
        "picking": {
            "tours_done": tours[0],
            "distance_m": round(float(tours[1]), 1),
            "stops": int(tours[2]),
            "picker_hours": round(float(tours[3]) / 3600, 2),
            "meters_per_stop": round(float(tours[1]) / int(tours[2]), 2) if tours[2] else None,
        },
        "open_tasks": open_tasks,
        "stock": {
            "pallets": stock[0],
            "cases": int(stock[1]),
            "allocated": int(stock[2]),
            "oversold_pallets": stock[3],
        },
        "inbound": await dock_stats(session, "inbound", "door_in"),
        "outbound": await dock_stats(session, "outbound", "door_out"),
    }


@router.get("/kpis")
async def get_kpis(sessions: SessionsDep) -> dict[str, Any]:
    async with sessions() as session:
        return await kpis(session)


@router.get("/events")
async def events(sessions: SessionsDep, limit: int = 50) -> list[dict[str, Any]]:
    async with sessions() as session:
        rows = await session.execute(
            select(Event.at, Event.kind, Event.ref, Event.data)
            .order_by(Event.id.desc())
            .limit(min(limit, 500))
        )
        return [{"at": stamp(r.at), "kind": r.kind, "ref": r.ref, "data": r.data} for r in rows]


@router.get("/layout")
async def get_layout(sessions: SessionsDep, layout: LayoutDep) -> dict[str, Any]:
    async with sessions() as session:
        doors = await session.execute(
            select(Location.code, Location.kind, Location.x, Location.y)
            .where(Location.kind.in_(("door_in", "door_out", "stage")))
            .order_by(Location.code)
        )
        places = [dict(row._mapping) for row in doors]
    cfg = layout.cfg
    return {
        "aisles": cfg.aisles,
        "bays": cfg.bays_per_aisle,
        "levels": cfg.levels,
        "pitch": cfg.aisle_pitch_m,
        "length": layout.length,
        "bay_width": cfg.bay_width_m,
        "end_gap": cfg.end_gap_m,
        "dock_depth": cfg.dock_depth_m,
        "depot": {"x": layout.x(layout.depot.aisle), "y": 0.0},
        "doors": places,
    }


@router.get("/slots")
async def slots(sessions: SessionsDep) -> list[dict[str, Any]]:
    async with sessions() as session:
        rows = await session.execute(
            select(
                Location.code,
                Location.aisle,
                Location.bay,
                Location.side,
                Location.x,
                Location.y,
                Sku.code.label("sku"),
                Sku.velocity,
                func.coalesce(func.sum(Stock.cases_on_hand - Stock.cases_allocated), 0).label("available"),
            )
            .outerjoin(Sku, Sku.pick_location_id == Location.id)
            .outerjoin(Stock, and_(Stock.location_id == Location.id, Stock.status == "available"))
            .where(Location.kind == "pick")
            .group_by(Location.id, Sku.id)
            .order_by(Location.aisle, Location.bay, Location.side)
        )
        return [dict(row._mapping) for row in rows]


@router.get("/skus")
async def skus(sessions: SessionsDep, q: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    async with sessions() as session:
        live = Stock.status.in_(("available", "received"))
        query = (
            select(
                Sku.code,
                Sku.description,
                Sku.category,
                Sku.velocity,
                Sku.cases_per_pallet,
                func.coalesce(func.sum(Stock.cases_on_hand).filter(live), 0).label("on_hand"),
                func.coalesce(func.sum(Stock.cases_allocated).filter(live), 0).label("allocated"),
                func.count(Stock.id).filter(live).label("pallets"),
            )
            .outerjoin(Stock, Stock.sku_id == Sku.id)
            .group_by(Sku.id)
            .order_by(Sku.code)
            .limit(min(limit, 1000))
        )
        if q:
            pattern = f"%{q.strip()}%"
            query = query.where(Sku.code.ilike(pattern) | Sku.description.ilike(pattern))
        return [dict(row._mapping) for row in await session.execute(query)]


@router.get("/skus/{code}")
async def sku_detail(code: str, sessions: SessionsDep) -> dict[str, Any]:
    async with sessions() as session:
        sku = await session.scalar(select(Sku).where(Sku.code == code))
        if sku is None:
            raise HTTPException(404, f"sku {code} not found")
        rows = await session.execute(
            select(
                Stock.lpn,
                Lot.code.label("lot"),
                Lot.expires_on,
                Location.code.label("location"),
                Location.kind,
                Stock.cases_on_hand,
                Stock.cases_allocated,
                Stock.status,
            )
            .join(Lot, Stock.lot_id == Lot.id)
            .join(Location, Stock.location_id == Location.id)
            .where(Stock.sku_id == sku.id, Stock.status != "empty")
            .order_by(Lot.expires_on, Stock.id)
        )
        slot = None
        if sku.pick_location_id is not None:
            slot = await session.scalar(select(Location.code).where(Location.id == sku.pick_location_id))
        return {
            "code": sku.code,
            "description": sku.description,
            "category": sku.category,
            "velocity": sku.velocity,
            "cases_per_pallet": sku.cases_per_pallet,
            "shelf_life_days": sku.shelf_life_days,
            "pick_slot": slot,
            "stock": [dict(row._mapping) for row in rows],
        }


@router.get("/waves")
async def waves(sessions: SessionsDep, limit: int = 100) -> list[dict[str, Any]]:
    async with sessions() as session:
        tours = (
            select(
                Tour.wave_id,
                func.count(Tour.id).label("tours"),
                func.count(Tour.id).filter(Tour.status == "done").label("done"),
                func.sum(Tour.distance_m).label("distance_m"),
            )
            .group_by(Tour.wave_id)
            .subquery()
        )
        orders = (
            select(Order.wave_id, func.count(Order.id).label("orders")).group_by(Order.wave_id).subquery()
        )
        rows = await session.execute(
            select(
                Wave.code,
                Wave.status,
                Wave.routing,
                Wave.batching,
                Wave.released_at,
                Appointment.code.label("appointment"),
                Appointment.window_end.label("departure"),
                func.coalesce(orders.c.orders, 0).label("orders"),
                func.coalesce(tours.c.tours, 0).label("tours"),
                func.coalesce(tours.c.done, 0).label("tours_done"),
                func.coalesce(tours.c.distance_m, 0.0).label("distance_m"),
            )
            .outerjoin(Appointment, Wave.appointment_id == Appointment.id)
            .outerjoin(tours, tours.c.wave_id == Wave.id)
            .outerjoin(orders, orders.c.wave_id == Wave.id)
            .order_by(Wave.id.desc())
            .limit(min(limit, 1000))
        )
        return [dict(row._mapping) for row in rows]


@router.get("/waves/{code}")
async def wave_detail(code: str, sessions: SessionsDep) -> dict[str, Any]:
    async with sessions() as session:
        wave = await session.scalar(select(Wave).where(Wave.code == code))
        if wave is None:
            raise HTTPException(404, f"wave {code} not found")
        tours = await session.execute(
            select(
                Tour.id,
                Tour.seq,
                Tour.status,
                Tour.worker,
                Tour.distance_m,
                Tour.stops,
                Tour.cases,
                Tour.started_at,
                Tour.done_at,
            )
            .where(Tour.wave_id == wave.id)
            .order_by(Tour.seq)
        )
        orders = await session.execute(
            select(Order.code, Customer.code.label("customer"), Order.status, Order.on_time, Order.in_full)
            .join(Customer, Order.customer_id == Customer.id)
            .where(Order.wave_id == wave.id)
            .order_by(Order.id)
        )
        return {
            "code": wave.code,
            "status": wave.status,
            "routing": wave.routing,
            "batching": wave.batching,
            "released_at": stamp(wave.released_at),
            "tours": [dict(row._mapping) for row in tours],
            "orders": [dict(row._mapping) for row in orders],
        }
