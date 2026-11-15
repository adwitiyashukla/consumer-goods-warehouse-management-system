from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, cast

from fastapi import APIRouter, HTTPException
from sqlalchemy import exists, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from wms.allocation import allocate
from wms.batching import Line, Unit, batch, split
from wms.config import Batching, Routing, Settings
from wms.db import log, now, pairs
from wms.deps import LayoutDep, SessionsDep, SettingsDep
from wms.layout import Layout, Point
from wms.models import (
    Allocation,
    Appointment,
    Customer,
    Location,
    Order,
    OrderLine,
    Sku,
    Stock,
    Task,
    Tour,
    Wave,
)
from wms.routing import route
from wms.schemas import At, OrderIn, WaveIn, stamp

router = APIRouter(prefix="/api", tags=["outbound"])

WAVE_LOCK = 73103
OPEN_REPLEN = text("kind = 'replen' and status in ('open', 'active')")


async def summary(session: AsyncSession, order: Order) -> dict[str, Any]:
    totals = (
        await session.execute(
            select(
                func.count(OrderLine.id),
                func.coalesce(func.sum(OrderLine.cases_ordered), 0),
                func.coalesce(func.sum(OrderLine.cases_allocated), 0),
                func.coalesce(func.sum(OrderLine.cases_picked), 0),
            ).where(OrderLine.order_id == order.id)
        )
    ).one()
    return {
        "code": order.code,
        "status": order.status,
        "ship_by": stamp(order.ship_by),
        "lines": totals[0],
        "cases": totals[1],
        "allocated": totals[2],
        "picked": totals[3],
        "short": totals[1] - totals[2],
    }


async def ship_order(session: AsyncSession, order: Order, at: datetime, on_trailer: bool) -> None:
    await session.execute(
        update(OrderLine).where(OrderLine.order_id == order.id).values(cases_shipped=OrderLine.cases_picked)
    )
    ordered, picked = (
        await session.execute(
            select(func.sum(OrderLine.cases_ordered), func.sum(OrderLine.cases_picked)).where(
                OrderLine.order_id == order.id
            )
        )
    ).one()
    order.status = "shipped"
    order.shipped_at = at
    order.on_time = on_trailer and at <= order.ship_by
    order.in_full = picked == ordered
    log(session, at, "order.shipped", order.code, on_time=order.on_time, in_full=order.in_full)


async def mark_picked(session: AsyncSession, order_ids: set[int], at: datetime) -> list[str]:
    if not order_ids:
        return []
    still_open = exists().where(
        Allocation.order_line_id == OrderLine.id, OrderLine.order_id == Order.id, Allocation.status == "open"
    )
    done = (
        await session.scalars(
            select(Order).where(Order.id.in_(order_ids), Order.status == "released", ~still_open)
        )
    ).all()
    for order in done:
        order.status = "picked"
        order.picked_at = at
        log(session, at, "order.picked", order.code)
    return [order.code for order in done]


async def replen_task(
    session: AsyncSession, stock: int, sku: int, source: int, target: int, cases: int, at: datetime
) -> int:
    created = await session.scalar(
        insert(Task)
        .values(
            kind="replen",
            status="open",
            priority=1,
            sku_id=sku,
            stock_id=stock,
            from_location_id=source,
            to_location_id=target,
            cases=cases,
            created_at=at,
        )
        .on_conflict_do_nothing(index_elements=["stock_id"], index_where=OPEN_REPLEN)
        .returning(Task.id)
    )
    if created is not None:
        return int(created)
    existing: int = (
        await session.scalars(
            select(Task.id).where(
                Task.stock_id == stock, Task.kind == "replen", Task.status.in_(("open", "active"))
            )
        )
    ).one()
    await session.execute(update(Task).where(Task.id == existing, Task.priority > 1).values(priority=1))
    return existing


async def release_wave(
    session: AsyncSession, settings: Settings, layout: Layout, wave: Wave, at: datetime
) -> dict[str, Any]:
    routing = cast(Routing, wave.routing)
    batching = cast(Batching, wave.batching)
    rows = (
        await session.execute(
            select(
                Allocation.id,
                Allocation.cases,
                Allocation.kind,
                Allocation.stock_id,
                OrderLine.sku_id,
                Order.id.label("order_id"),
                Order.code.label("order_code"),
                Sku.pick_location_id,
                Sku.case_volume_m3,
                Stock.location_id,
                Stock.cases_on_hand,
                Location.kind.label("stock_kind"),
            )
            .join(OrderLine, Allocation.order_line_id == OrderLine.id)
            .join(Order, OrderLine.order_id == Order.id)
            .join(Sku, OrderLine.sku_id == Sku.id)
            .join(Stock, Allocation.stock_id == Stock.id)
            .join(Location, Stock.location_id == Location.id)
            .where(Order.wave_id == wave.id, Allocation.status == "open")
            .order_by(Order.created_at, Order.id, OrderLine.line_no, Allocation.id)
        )
    ).all()
    slot_ids = {row.pick_location_id for row in rows if row.pick_location_id is not None}
    slots = {
        row.id: Point(row.aisle, row.y)
        for row in await session.execute(
            select(Location.id, Location.aisle, Location.y).where(Location.id.in_(slot_ids))
        )
    }
    door = None
    if wave.appointment_id is not None:
        door = (await session.get_one(Appointment, wave.appointment_id)).door_id
    if door is None:
        door = await session.scalar(select(Location.id).where(Location.kind == "stage"))
    info = {row.id: row for row in rows}
    needs: dict[int, int] = {}
    by_order: dict[int, list[Line]] = {}
    codes: dict[int, str] = {}
    pallets = 0
    for row in rows:
        if row.kind == "pallet":
            session.add(
                Task(
                    kind="pallet",
                    priority=2,
                    wave_id=wave.id,
                    sku_id=row.sku_id,
                    stock_id=row.stock_id,
                    allocation_id=row.id,
                    from_location_id=row.location_id,
                    to_location_id=door,
                    cases=row.cases,
                    created_at=at,
                )
            )
            pallets += 1
            continue
        if row.pick_location_id is None:
            raise HTTPException(409, f"sku of order {row.order_code} has no pick slot")
        if row.stock_kind == "reserve":
            needs[row.id] = await replen_task(
                session,
                row.stock_id,
                row.sku_id,
                row.location_id,
                row.pick_location_id,
                row.cases_on_hand,
                at,
            )
        by_order.setdefault(row.order_id, []).append(
            Line(row.id, slots[row.pick_location_id], row.cases * row.case_volume_m3)
        )
        codes[row.order_id] = row.order_code
    capacity = settings.picking.tour_capacity_m3
    units: list[Unit] = []
    for order_id, lines in by_order.items():
        units.extend(split(layout, codes[order_id], lines, capacity))
    groups = batch(layout, units, capacity, settings.picking.tour_max_orders, batching, routing)
    distance = 0.0
    for seq, group in enumerate(groups, 1):
        lines = [line for index in group for line in units[index].lines]
        plan = route(layout, [line.stop for line in lines], routing)
        blocked = len({needs[line.ref] for line in lines if line.ref in needs})
        tour = Tour(
            wave_id=wave.id,
            seq=seq,
            status="waiting" if blocked else "ready",
            routing=wave.routing,
            blocked=blocked,
            distance_m=plan.distance,
            stops=len({line.stop for line in lines}),
            cases=sum(info[line.ref].cases for line in lines),
            volume_m3=sum(line.volume for line in lines),
        )
        session.add(tour)
        await session.flush()
        for position, index in enumerate(plan.order):
            row = info[lines[index].ref]
            session.add(
                Task(
                    kind="pick",
                    priority=0,
                    wave_id=wave.id,
                    tour_id=tour.id,
                    seq=position,
                    sku_id=row.sku_id,
                    stock_id=row.stock_id,
                    allocation_id=row.id,
                    from_location_id=row.pick_location_id,
                    to_location_id=door,
                    cases=row.cases,
                    created_at=at,
                )
            )
        distance += plan.distance
    wave.status = "released"
    wave.released_at = at
    order_ids = set(
        (await session.scalars(select(Order.id).where(Order.wave_id == wave.id).with_for_update())).all()
    )
    await session.execute(update(Order).where(Order.id.in_(order_ids)).values(status="released"))
    await mark_picked(session, order_ids, at)
    result = {
        "wave": wave.code,
        "orders": len(order_ids),
        "tours": len(groups),
        "pallets": pallets,
        "replens": len(set(needs.values())),
        "distance_m": round(distance, 1),
    }
    log(session, at, "wave.released", wave.code, **result)
    return result


@router.post("/orders", status_code=201)
async def create_order(body: OrderIn, sessions: SessionsDep, settings: SettingsDep) -> dict[str, Any]:
    at = body.at or now()
    async with sessions() as session, session.begin():
        if await session.scalar(select(Order.id).where(Order.code == body.code)) is not None:
            raise HTTPException(409, f"order {body.code} already exists")
        customer = await session.scalar(select(Customer).where(Customer.code == body.customer))
        if customer is None:
            raise HTTPException(404, f"customer {body.customer} not found")
        appointment_id = None
        if body.appointment is not None:
            appointment_id = await session.scalar(
                select(Appointment.id).where(
                    Appointment.code == body.appointment, Appointment.direction == "outbound"
                )
            )
            if appointment_id is None:
                raise HTTPException(404, f"outbound appointment {body.appointment} not found")
        wanted: dict[str, int] = {}
        for line in body.lines:
            wanted[line.sku] = wanted.get(line.sku, 0) + line.cases
        skus = await pairs(session, select(Sku.code, Sku.id).where(Sku.code.in_(wanted)))
        missing = sorted(wanted.keys() - skus.keys())
        if missing:
            raise HTTPException(404, f"unknown sku {missing[0]}")
        order = Order(
            code=body.code,
            customer_id=customer.id,
            appointment_id=appointment_id,
            ship_by=body.ship_by,
            created_at=at,
            status="open",
        )
        session.add(order)
        await session.flush()
        for number, (sku, cases) in enumerate(wanted.items(), 1):
            session.add(OrderLine(order_id=order.id, line_no=number, sku_id=skus[sku], cases_ordered=cases))
        await session.flush()
        log(session, at, "order.created", order.code, lines=len(wanted))
        if body.allocate:
            await allocate(session, order, settings.picking.full_pallets, at)
        result = await summary(session, order)
    return result


@router.post("/orders/{code}/allocate")
async def allocate_order(code: str, body: At, sessions: SessionsDep, settings: SettingsDep) -> dict[str, Any]:
    async with sessions() as session, session.begin():
        order = await session.scalar(select(Order).where(Order.code == code).with_for_update())
        if order is None:
            raise HTTPException(404, f"order {code} not found")
        if order.status not in ("open", "allocated"):
            raise HTTPException(409, f"order {code} is already {order.status}")
        await allocate(session, order, settings.picking.full_pallets, body.at or now())
        return await summary(session, order)


@router.post("/orders/{code}/ship")
async def ship_late(code: str, body: At, sessions: SessionsDep) -> dict[str, Any]:
    async with sessions() as session, session.begin():
        order = await session.scalar(select(Order).where(Order.code == code).with_for_update())
        if order is None:
            raise HTTPException(404, f"order {code} not found")
        if order.status != "picked":
            raise HTTPException(409, f"order {code} is {order.status}, not picked")
        await ship_order(session, order, body.at or now(), on_trailer=False)
        return {"code": order.code, "on_time": order.on_time, "in_full": order.in_full}


@router.get("/orders")
async def list_orders(
    sessions: SessionsDep, status: str | None = None, limit: int = 200
) -> list[dict[str, Any]]:
    async with sessions() as session:
        query = (
            select(
                Order.code,
                Order.status,
                Order.ship_by,
                Order.shipped_at,
                Order.on_time,
                Order.in_full,
                Customer.code.label("customer"),
                Appointment.code.label("appointment"),
                Wave.code.label("wave"),
            )
            .join(Customer, Order.customer_id == Customer.id)
            .outerjoin(Appointment, Order.appointment_id == Appointment.id)
            .outerjoin(Wave, Order.wave_id == Wave.id)
            .order_by(Order.id.desc())
            .limit(min(limit, 2000))
        )
        if status is not None:
            query = query.where(Order.status == status)
        return [dict(row._mapping) for row in await session.execute(query)]


@router.get("/orders/{code}")
async def get_order(code: str, sessions: SessionsDep) -> dict[str, Any]:
    async with sessions() as session:
        order = await session.scalar(select(Order).where(Order.code == code))
        if order is None:
            raise HTTPException(404, f"order {code} not found")
        lines = await session.execute(
            select(
                OrderLine.line_no,
                Sku.code.label("sku"),
                OrderLine.cases_ordered,
                OrderLine.cases_allocated,
                OrderLine.cases_picked,
                OrderLine.cases_shipped,
            )
            .join(Sku, OrderLine.sku_id == Sku.id)
            .where(OrderLine.order_id == order.id)
            .order_by(OrderLine.line_no)
        )
        result = await summary(session, order)
        result["lines"] = [dict(row._mapping) for row in lines]
        result["on_time"] = order.on_time
        result["in_full"] = order.in_full
        return result


@router.post("/waves", status_code=201)
async def create_wave(
    body: WaveIn, sessions: SessionsDep, settings: SettingsDep, layout: LayoutDep
) -> dict[str, Any]:
    at = body.at or now()
    async with sessions() as session, session.begin():
        await session.execute(text("select pg_advisory_xact_lock(:key)"), {"key": WAVE_LOCK})
        query = select(Order).where(Order.status == "allocated")
        appointment = None
        if body.appointment is not None:
            appointment = await session.scalar(
                select(Appointment).where(
                    Appointment.code == body.appointment, Appointment.direction == "outbound"
                )
            )
            if appointment is None:
                raise HTTPException(404, f"outbound appointment {body.appointment} not found")
            query = query.where(Order.appointment_id == appointment.id)
        orders = (
            await session.scalars(
                query.order_by(Order.created_at, Order.id).with_for_update(skip_locked=True)
            )
        ).all()
        if not orders:
            raise HTTPException(409, "there are no allocated orders to wave")
        count = await session.scalar(select(func.count(Wave.id)))
        wave = Wave(
            code=f"W{(count or 0) + 1:04d}",
            appointment_id=appointment.id if appointment else None,
            routing=settings.picking.routing,
            batching=settings.picking.batching,
            planned_at=at,
        )
        session.add(wave)
        await session.flush()
        for order in orders:
            order.wave_id = wave.id
            order.status = "waved"
        log(session, at, "wave.planned", wave.code, orders=len(orders))
        result: dict[str, Any] = {"wave": wave.code, "orders": len(orders), "status": "planned"}
        if body.release:
            await session.flush()
            result = {**await release_wave(session, settings, layout, wave, at), "status": "released"}
    return result


@router.post("/waves/{code}/release")
async def release(
    code: str, body: At, sessions: SessionsDep, settings: SettingsDep, layout: LayoutDep
) -> dict[str, Any]:
    async with sessions() as session, session.begin():
        wave = await session.scalar(select(Wave).where(Wave.code == code).with_for_update())
        if wave is None:
            raise HTTPException(404, f"wave {code} not found")
        if wave.status != "planned":
            raise HTTPException(409, f"wave {code} is already {wave.status}")
        return {**await release_wave(session, settings, layout, wave, body.at or now()), "status": "released"}


@router.post("/appointments/{code}/ship")
async def ship_trailer(code: str, body: At, sessions: SessionsDep, settings: SettingsDep) -> dict[str, Any]:
    at = body.at or now()
    async with sessions() as session, session.begin():
        appointment = await session.scalar(
            select(Appointment).where(Appointment.code == code).with_for_update()
        )
        if appointment is None or appointment.direction != "outbound":
            raise HTTPException(404, f"outbound appointment {code} not found")
        cutoff = at - timedelta(minutes=settings.schedule.load_cutoff_minutes)
        ready = (
            await session.scalars(
                select(Order)
                .where(
                    Order.appointment_id == appointment.id,
                    Order.status == "picked",
                    Order.picked_at <= cutoff,
                )
                .order_by(Order.id)
                .with_for_update()
            )
        ).all()
        for order in ready:
            await ship_order(session, order, at, on_trailer=True)
        waiting = (
            await session.execute(
                select(Order.code, Order.status)
                .where(Order.appointment_id == appointment.id, Order.status != "shipped")
                .order_by(Order.id)
            )
        ).all()
        log(session, at, "trailer.shipped", appointment.code, orders=len(ready), left=len(waiting))
        return {
            "appointment": code,
            "shipped": [order.code for order in ready],
            "left": [{"code": row.code, "status": row.status} for row in waiting],
        }
