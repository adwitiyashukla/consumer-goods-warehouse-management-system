from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import exists, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from wms import idempotency
from wms.db import log, now, pairs
from wms.deps import LayoutDep, SessionsDep
from wms.layout import Layout, Point
from wms.models import Appointment, Asn, AsnLine, Location, Lot, Sku, Stock, Task
from wms.schemas import AsnIn, ReceiveIn

router = APIRouter(prefix="/api", tags=["inbound"])

PUTAWAY_LOCK = 73102


async def lot_id(session: AsyncSession, sku_id: int, code: str, produced: date, expires: date) -> int:
    await session.execute(
        insert(Lot)
        .values(sku_id=sku_id, code=code, produced_on=produced, expires_on=expires)
        .on_conflict_do_nothing(index_elements=["sku_id", "code"])
    )
    return (await session.scalars(select(Lot.id).where(Lot.sku_id == sku_id, Lot.code == code))).one()


async def free_reserve(session: AsyncSession) -> list[tuple[int, Point]]:
    occupied = exists().where(Stock.location_id == Location.id, Stock.status != "empty")
    targeted = exists().where(
        Task.to_location_id == Location.id,
        Task.kind == "putaway",
        Task.status.in_(("open", "active")),
    )
    rows = await session.execute(
        select(Location.id, Location.aisle, Location.y)
        .where(Location.kind == "reserve", ~occupied, ~targeted)
        .order_by(Location.id)
    )
    return [(row.id, Point(row.aisle, row.y)) for row in rows]


def nearest(layout: Layout, free: list[tuple[int, Point]], target: Point) -> int:
    best = min(range(len(free)), key=lambda i: (layout.distance(free[i][1], target), free[i][0]))
    return free.pop(best)[0]


async def receive_asn(
    session: AsyncSession, layout: Layout, asn: Asn, counts: dict[tuple[int, str], int] | None, at: datetime
) -> dict[str, Any]:
    await session.execute(text("select pg_advisory_xact_lock(:key)"), {"key": PUTAWAY_LOCK})
    door_id: int | None = None
    if asn.appointment_id is not None:
        door_id = (await session.get_one(Appointment, asn.appointment_id)).door_id
    if door_id is None:
        door_id = (
            await session.scalars(
                select(Location.id).where(Location.kind == "door_in").order_by(Location.code)
            )
        ).first()
    if door_id is None:
        raise HTTPException(409, "there is no inbound door")
    rows = (
        await session.execute(
            select(AsnLine, Sku, Location.aisle, Location.y)
            .join(Sku, AsnLine.sku_id == Sku.id)
            .outerjoin(Location, Sku.pick_location_id == Location.id)
            .where(AsnLine.asn_id == asn.id)
            .order_by(AsnLine.id)
        )
    ).all()
    free = await free_reserve(session)
    pallets = 0
    variance = 0
    for line, sku, aisle, y in rows:
        cases = line.cases_expected if counts is None else counts.get((sku.id, line.lot_code), 0)
        line.cases_received = cases
        variance += cases - line.cases_expected
        lot = await lot_id(session, sku.id, line.lot_code, line.produced_on, line.expires_on)
        target = Point(aisle, y) if aisle is not None else layout.depot
        while cases > 0:
            size = min(cases, sku.cases_per_pallet)
            cases -= size
            pallets += 1
            if not free:
                raise HTTPException(409, "the reserve racks are full")
            stock = Stock(
                lpn=f"{asn.code}-{pallets:02d}",
                sku_id=sku.id,
                lot_id=lot,
                location_id=door_id,
                cases_on_hand=size,
                status="received",
            )
            session.add(stock)
            await session.flush()
            session.add(
                Task(
                    kind="putaway",
                    priority=3,
                    sku_id=sku.id,
                    stock_id=stock.id,
                    from_location_id=door_id,
                    to_location_id=nearest(layout, free, target),
                    cases=size,
                    created_at=at,
                )
            )
    asn.status = "received"
    asn.received_at = at
    log(session, at, "asn.received", asn.code, pallets=pallets, variance=variance)
    return {"asn": asn.code, "pallets": pallets, "variance": variance}


@router.post("/asns", status_code=201)
async def create_asn(body: AsnIn, sessions: SessionsDep) -> dict[str, Any]:
    async with sessions() as session, session.begin():
        if await session.scalar(select(Asn.id).where(Asn.code == body.code)) is not None:
            raise HTTPException(409, f"asn {body.code} already exists")
        appointment_id = None
        if body.appointment is not None:
            appointment_id = await session.scalar(
                select(Appointment.id).where(
                    Appointment.code == body.appointment, Appointment.direction == "inbound"
                )
            )
            if appointment_id is None:
                raise HTTPException(404, f"inbound appointment {body.appointment} not found")
        codes = {line.sku for line in body.lines}
        skus = await pairs(session, select(Sku.code, Sku.id).where(Sku.code.in_(codes)))
        missing = sorted(codes - skus.keys())
        if missing:
            raise HTTPException(404, f"unknown sku {missing[0]}")
        asn = Asn(code=body.code, origin=body.origin, appointment_id=appointment_id)
        session.add(asn)
        await session.flush()
        for line in body.lines:
            if line.expires_on <= line.produced_on:
                raise HTTPException(422, f"lot {line.lot} expires before it was made")
            session.add(
                AsnLine(
                    asn_id=asn.id,
                    sku_id=skus[line.sku],
                    lot_code=line.lot,
                    produced_on=line.produced_on,
                    expires_on=line.expires_on,
                    cases_expected=line.cases,
                )
            )
        log(session, body.at or now(), "asn.created", asn.code, lines=len(body.lines))
    return {"id": asn.id, "code": asn.code, "lines": len(body.lines)}


@router.post("/asns/{code}/receive")
async def receive(
    code: str, body: ReceiveIn, sessions: SessionsDep, layout: LayoutDep, key: idempotency.Key = None
) -> dict[str, Any]:
    payload = {"asn": code, **body.model_dump(mode="json")}
    async with sessions() as session, session.begin():
        cached = await idempotency.replay(session, key, payload)
        if cached is not None:
            return cached
        asn = await session.scalar(select(Asn).where(Asn.code == code).with_for_update())
        if asn is None:
            raise HTTPException(404, f"asn {code} not found")
        if asn.status != "expected":
            raise HTTPException(409, f"asn {code} was already received")
        counts: dict[tuple[int, str], int] | None = None
        if body.lines is not None:
            codes = {line.sku for line in body.lines}
            skus = await pairs(session, select(Sku.code, Sku.id).where(Sku.code.in_(codes)))
            counts = {}
            for line in body.lines:
                if line.sku not in skus:
                    raise HTTPException(404, f"unknown sku {line.sku}")
                pair = (skus[line.sku], line.lot)
                counts[pair] = counts.get(pair, 0) + line.cases
        result = await receive_asn(session, layout, asn, counts, body.at or now())
        await idempotency.remember(session, key, payload, result)
    return result


@router.get("/asns")
async def list_asns(sessions: SessionsDep, limit: int = 100) -> list[dict[str, Any]]:
    async with sessions() as session:
        rows = await session.execute(
            select(Asn.code, Asn.origin, Asn.status, Asn.received_at, Appointment.code.label("appointment"))
            .outerjoin(Appointment, Asn.appointment_id == Appointment.id)
            .order_by(Asn.id.desc())
            .limit(min(limit, 1000))
        )
        return [dict(row._mapping) for row in rows]
