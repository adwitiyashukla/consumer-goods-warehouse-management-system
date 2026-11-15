from __future__ import annotations

import random
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import exists, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from wms import idempotency
from wms.config import ROUTINGS, Settings
from wms.db import log, now
from wms.deps import LayoutDep, SessionsDep, SettingsDep
from wms.layout import Point
from wms.models import (
    Allocation,
    Appointment,
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
from wms.outbound import OPEN_REPLEN, mark_picked
from wms.routing import route
from wms.schemas import At, CompleteIn, CountIn, NextIn, stamp

router = APIRouter(prefix="/api", tags=["tasks"])

DRIVER_KINDS = ("replen", "pallet", "putaway", "count")
REPLEN_LOCK = 73104


async def task_view(session: AsyncSession, task: Task) -> dict[str, Any]:
    ids = [i for i in (task.from_location_id, task.to_location_id) if i is not None]
    places = {
        row.id: row
        for row in await session.execute(
            select(Location.id, Location.code, Location.x, Location.y).where(Location.id.in_(ids))
        )
    }
    source = places.get(task.from_location_id) if task.from_location_id else None
    target = places.get(task.to_location_id) if task.to_location_id else None
    sku = await session.scalar(select(Sku.code).where(Sku.id == task.sku_id)) if task.sku_id else None
    return {
        "id": task.id,
        "kind": task.kind,
        "status": task.status,
        "sku": sku,
        "cases": task.cases,
        "from": {"code": source.code, "x": source.x, "y": source.y} if source else None,
        "to": {"code": target.code, "x": target.x, "y": target.y} if target else None,
    }


async def tour_view(session: AsyncSession, tour: Tour) -> dict[str, Any]:
    rows = await session.execute(
        select(
            Task.id,
            Task.seq,
            Task.status,
            Task.cases,
            Sku.code.label("sku"),
            Location.code.label("location"),
            Location.aisle,
            Location.y,
        )
        .join(Sku, Task.sku_id == Sku.id)
        .join(Location, Task.from_location_id == Location.id)
        .where(Task.tour_id == tour.id)
        .order_by(Task.seq)
    )
    wave = await session.scalar(select(Wave.code).where(Wave.id == tour.wave_id))
    return {
        "id": tour.id,
        "wave": wave,
        "status": tour.status,
        "worker": tour.worker,
        "routing": tour.routing,
        "distance_m": round(tour.distance_m, 1),
        "stops": tour.stops,
        "cases": tour.cases,
        "started_at": stamp(tour.started_at),
        "done_at": stamp(tour.done_at),
        "tasks": [dict(row._mapping) for row in rows],
    }


async def finish_pick(session: AsyncSession, task: Task, cases: int | None, at: datetime) -> dict[str, Any]:
    if task.allocation_id is None:
        raise HTTPException(409, f"task {task.id} has no allocation")
    allocation = await session.get_one(Allocation, task.allocation_id, with_for_update=True)
    stock = await session.get_one(Stock, allocation.stock_id, with_for_update=True)
    if task.kind == "pick" and stock.location_id != task.from_location_id:
        raise HTTPException(409, f"pallet {stock.lpn} is not in the pick slot yet")
    picked = allocation.cases if cases is None else min(cases, allocation.cases)
    stock.cases_on_hand -= picked
    stock.cases_allocated -= allocation.cases
    if task.kind == "pallet" and task.to_location_id is not None:
        stock.location_id = task.to_location_id
    if stock.cases_on_hand == 0:
        stock.status = "empty"
    elif picked < allocation.cases:
        session.add(
            Task(
                kind="count",
                priority=2,
                sku_id=stock.sku_id,
                stock_id=stock.id,
                from_location_id=stock.location_id,
                to_location_id=stock.location_id,
                cases=stock.cases_on_hand,
                created_at=at,
            )
        )
        log(session, at, "pick.short", stock.lpn, wanted=allocation.cases, picked=picked)
    line = await session.get_one(OrderLine, allocation.order_line_id, with_for_update=True)
    line.cases_picked += picked
    allocation.status = "picked"
    order = await session.get_one(Order, line.order_id, with_for_update=True)
    result: dict[str, Any] = {"cases": picked, "order": order.code, "tour_done": False}
    if task.tour_id is not None:
        left = await session.scalar(
            select(func.count(Task.id)).where(
                Task.tour_id == task.tour_id, Task.status != "done", Task.id != task.id
            )
        )
        if left == 0:
            await session.execute(
                update(Tour).where(Tour.id == task.tour_id).values(status="done", done_at=at)
            )
            result["tour_done"] = True
    await session.flush()
    result["order_picked"] = bool(await mark_picked(session, {order.id}, at))
    return result


async def finish_replen(session: AsyncSession, task: Task) -> dict[str, Any]:
    stock = await session.get_one(Stock, task.stock_id, with_for_update=True)
    if task.to_location_id is not None:
        stock.location_id = task.to_location_id
    waiting = (
        await session.scalars(
            select(Task.tour_id)
            .where(Task.kind == "pick", Task.status == "open", Task.stock_id == stock.id)
            .distinct()
        )
    ).all()
    if waiting:
        await session.execute(update(Tour).where(Tour.id.in_(waiting)).values(blocked=Tour.blocked - 1))
        await session.execute(
            update(Tour)
            .where(Tour.id.in_(waiting), Tour.blocked <= 0, Tour.status == "waiting")
            .values(status="ready")
        )
    return {"unblocked": len(waiting)}


async def finish_count(
    session: AsyncSession, task: Task, counted: int | None, at: datetime
) -> dict[str, Any]:
    if counted is None:
        raise HTTPException(422, "a count task needs the counted cases")
    stock = await session.get_one(Stock, task.stock_id, with_for_update=True)
    task.counted = counted
    variance = counted - stock.cases_on_hand
    if variance:
        stock.cases_on_hand = max(counted, stock.cases_allocated)
        if stock.cases_on_hand == 0:
            stock.status = "empty"
        log(session, at, "count.variance", stock.lpn, variance=variance)
    return {"variance": variance}


@router.post("/tasks/next")
async def next_task(body: NextIn, sessions: SessionsDep) -> dict[str, Any]:
    at = body.at or now()
    async with sessions() as session, session.begin():
        if body.role == "picker":
            tour = await session.scalar(
                select(Tour).where(Tour.worker == body.worker, Tour.status == "active")
            )
            if tour is None:
                tour = await session.scalar(
                    select(Tour)
                    .join(Wave, Tour.wave_id == Wave.id)
                    .outerjoin(Appointment, Wave.appointment_id == Appointment.id)
                    .where(Tour.status == "ready")
                    .order_by(Appointment.window_end.asc().nulls_last(), Tour.wave_id, Tour.seq)
                    .limit(1)
                    .with_for_update(of=Tour, skip_locked=True)
                )
                if tour is None:
                    return {"tour": None}
                tour.status = "active"
                tour.worker = body.worker
                tour.started_at = at
                await session.execute(
                    update(Task)
                    .where(Task.tour_id == tour.id, Task.status == "open")
                    .values(status="active", worker=body.worker, started_at=at)
                )
                log(session, at, "tour.started", str(tour.id), worker=body.worker)
            return {"tour": await tour_view(session, tour)}
        task = await session.scalar(
            select(Task).where(
                Task.worker == body.worker, Task.status == "active", Task.kind.in_(DRIVER_KINDS)
            )
        )
        if task is None:
            task = await session.scalar(
                select(Task)
                .where(Task.status == "open", Task.kind.in_(DRIVER_KINDS))
                .order_by(Task.priority, Task.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if task is None:
                return {"task": None}
            task.status = "active"
            task.worker = body.worker
            task.started_at = at
        return {"task": await task_view(session, task)}


@router.post("/tasks/{task_id}/complete")
async def complete(
    task_id: int, body: CompleteIn, sessions: SessionsDep, key: idempotency.Key = None
) -> dict[str, Any]:
    payload = {"task": task_id, **body.model_dump(mode="json")}
    at = body.at or now()
    async with sessions() as session, session.begin():
        cached = await idempotency.replay(session, key, payload)
        if cached is not None:
            return cached
        task = await session.get(Task, task_id, with_for_update=True)
        if task is None:
            raise HTTPException(404, f"task {task_id} not found")
        if task.status == "done":
            raise HTTPException(409, f"task {task_id} is already done")
        if task.kind == "pick" and task.status != "active":
            raise HTTPException(409, f"task {task_id} belongs to a tour nobody has started")
        result: dict[str, Any] = {"task": task.id, "kind": task.kind}
        if task.kind in ("pick", "pallet"):
            result.update(await finish_pick(session, task, body.cases, at))
        elif task.kind == "putaway":
            stock = await session.get_one(Stock, task.stock_id, with_for_update=True)
            if task.to_location_id is not None:
                stock.location_id = task.to_location_id
            stock.status = "available"
        elif task.kind == "replen":
            result.update(await finish_replen(session, task))
        else:
            result.update(await finish_count(session, task, body.counted, at))
        task.status = "done"
        task.done_at = at
        task.worker = task.worker or body.worker
        if task.started_at is None:
            task.started_at = at
        await idempotency.remember(session, key, payload, result)
    return result


@router.post("/replenishment/run")
async def run_replenishment(body: At, sessions: SessionsDep, settings: SettingsDep) -> dict[str, Any]:
    at = body.at or now()
    async with sessions() as session, session.begin():
        created = await replenish(session, settings, at)
    return {"created": created}


async def replenish(session: AsyncSession, settings: Settings, at: datetime) -> int:
    await session.execute(text("select pg_advisory_xact_lock(:key)"), {"key": REPLEN_LOCK})
    in_slot = func.coalesce(
        func.sum(Stock.cases_on_hand - Stock.cases_allocated).filter(
            Stock.location_id == Sku.pick_location_id, Stock.status == "available"
        ),
        0,
    )
    rows = (
        await session.execute(
            select(Sku.id, Sku.pick_location_id, Sku.cases_per_pallet, in_slot.label("available"))
            .outerjoin(Stock, Stock.sku_id == Sku.id)
            .where(Sku.pick_location_id.is_not(None))
            .group_by(Sku.id)
        )
    ).all()
    pending = set(
        (
            await session.scalars(
                select(Task.to_location_id).where(Task.kind == "replen", Task.status.in_(("open", "active")))
            )
        ).all()
    )
    low = {
        row.id: row.pick_location_id
        for row in rows
        if row.available < settings.slotting.replen_trigger * row.cases_per_pallet
        and row.pick_location_id not in pending
    }
    if not low:
        return 0
    rank = func.row_number().over(partition_by=Stock.sku_id, order_by=(Lot.expires_on, Stock.id))
    ranked = (
        select(Stock.sku_id, Stock.id, Stock.location_id, Stock.cases_on_hand, rank.label("rank"))
        .join(Lot, Stock.lot_id == Lot.id)
        .join(Location, Stock.location_id == Location.id)
        .where(
            Stock.sku_id.in_(low),
            Location.kind == "reserve",
            Stock.status == "available",
            Stock.cases_allocated < Stock.cases_on_hand,
        )
        .subquery()
    )
    pallets = (
        await session.execute(
            select(ranked.c.sku_id, ranked.c.id, ranked.c.location_id, ranked.c.cases_on_hand)
            .where(ranked.c.rank == 1)
            .order_by(ranked.c.sku_id)
        )
    ).all()
    created = 0
    for pallet in pallets:
        made = await session.scalar(
            insert(Task)
            .values(
                kind="replen",
                status="open",
                priority=4,
                sku_id=pallet.sku_id,
                stock_id=pallet.id,
                from_location_id=pallet.location_id,
                to_location_id=low[pallet.sku_id],
                cases=pallet.cases_on_hand,
                created_at=at,
            )
            .on_conflict_do_nothing(index_elements=["stock_id"], index_where=OPEN_REPLEN)
            .returning(Task.id)
        )
        created += made is not None
    log(session, at, "replen.run", "slots", created=created)
    return created


@router.post("/counts", status_code=201)
async def create_counts(body: CountIn, sessions: SessionsDep) -> dict[str, Any]:
    at = body.at or now()
    async with sessions() as session, session.begin():
        open_count = exists().where(
            Task.stock_id == Stock.id, Task.kind == "count", Task.status.in_(("open", "active"))
        )
        rows = (
            await session.execute(
                select(Stock.id, Stock.sku_id, Stock.location_id, Stock.cases_on_hand)
                .join(Location, Stock.location_id == Location.id)
                .where(
                    Location.kind.in_(("pick", "reserve")),
                    Stock.status == "available",
                    Stock.cases_allocated == 0,
                    ~open_count,
                )
                .order_by(Stock.id)
            )
        ).all()
        chosen = random.Random(body.seed).sample(rows, min(body.count, len(rows)))
        for row in chosen:
            session.add(
                Task(
                    kind="count",
                    priority=5,
                    sku_id=row.sku_id,
                    stock_id=row.id,
                    from_location_id=row.location_id,
                    to_location_id=row.location_id,
                    cases=row.cases_on_hand,
                    created_at=at,
                )
            )
        log(session, at, "count.created", "stock", tasks=len(chosen))
    return {"created": len(chosen)}


@router.get("/tours")
async def list_tours(
    sessions: SessionsDep, wave: str | None = None, status: str | None = None, limit: int = 200
) -> list[dict[str, Any]]:
    async with sessions() as session:
        query = (
            select(
                Tour.id,
                Wave.code.label("wave"),
                Tour.seq,
                Tour.status,
                Tour.worker,
                Tour.routing,
                Tour.distance_m,
                Tour.stops,
                Tour.cases,
                Tour.started_at,
                Tour.done_at,
            )
            .join(Wave, Tour.wave_id == Wave.id)
            .order_by(Tour.id.desc())
            .limit(min(limit, 2000))
        )
        if wave is not None:
            query = query.where(Wave.code == wave)
        if status is not None:
            query = query.where(Tour.status == status)
        return [dict(row._mapping) for row in await session.execute(query)]


@router.get("/tours/{tour_id}")
async def get_tour(tour_id: int, sessions: SessionsDep) -> dict[str, Any]:
    async with sessions() as session:
        tour = await session.get(Tour, tour_id)
        if tour is None:
            raise HTTPException(404, f"tour {tour_id} not found")
        return await tour_view(session, tour)


@router.get("/tours/{tour_id}/routes")
async def tour_routes(tour_id: int, sessions: SessionsDep, layout: LayoutDep) -> dict[str, Any]:
    async with sessions() as session:
        tour = await session.get(Tour, tour_id)
        if tour is None:
            raise HTTPException(404, f"tour {tour_id} not found")
        rows = (
            await session.execute(
                select(Location.code, Location.side, Location.aisle, Location.y)
                .join(Task, Task.from_location_id == Location.id)
                .where(Task.tour_id == tour_id)
                .order_by(Task.seq)
            )
        ).all()
    seen: dict[str, tuple[str | None, Point]] = {}
    for row in rows:
        seen.setdefault(row.code, (row.side, Point(row.aisle, row.y)))
    stops = [point for _, point in seen.values()]
    routes = {}
    for policy in ROUTINGS:
        plan = route(layout, stops, policy)
        routes[policy] = {
            "distance_m": round(plan.distance, 1),
            "order": plan.order,
            "path": [[layout.x(point.aisle), point.y] for point in plan.path],
        }
    return {
        "tour": tour_id,
        "routing": tour.routing,
        "stops": [
            {"code": code, "side": side, "x": layout.x(p.aisle), "y": p.y} for code, (side, p) in seen.items()
        ],
        "routes": routes,
    }
