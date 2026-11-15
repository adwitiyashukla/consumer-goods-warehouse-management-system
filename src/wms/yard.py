from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wms.db import log, now, pairs
from wms.deps import SessionsDep, SettingsDep
from wms.models import Appointment, Location
from wms.schemas import AppointmentIn, At, DockIn, stamp

router = APIRouter(prefix="/api", tags=["yard"])


@dataclass(frozen=True)
class Slot:
    key: str
    earliest: datetime
    minutes: float


def assign_doors(
    slots: Sequence[Slot], doors: Sequence[str], turn_minutes: float
) -> dict[str, tuple[str, datetime]]:
    if not doors:
        raise ValueError("no doors to schedule")
    free: dict[str, datetime | None] = dict.fromkeys(doors)
    plan: dict[str, tuple[str, datetime]] = {}
    for slot in sorted(slots, key=lambda s: (s.earliest, s.key)):

        def ready(door: str, slot: Slot = slot) -> datetime:
            busy = free[door]
            return slot.earliest if busy is None else max(busy, slot.earliest)

        door = min(doors, key=lambda d: (ready(d), d))
        start = ready(door)
        free[door] = start + timedelta(minutes=slot.minutes + turn_minutes)
        plan[slot.key] = (door, start)
    return plan


def occupancy(appointment: Appointment) -> float:
    if appointment.direction == "outbound":
        return (appointment.window_end - appointment.window_start).total_seconds() / 60
    return appointment.duration_minutes


async def view(session: AsyncSession, appointment: Appointment) -> dict[str, Any]:
    door = None
    if appointment.door_id is not None:
        door = await session.scalar(select(Location.code).where(Location.id == appointment.door_id))
    return {
        "code": appointment.code,
        "direction": appointment.direction,
        "carrier": appointment.carrier,
        "lane": appointment.lane,
        "status": appointment.status,
        "door": door,
        "window_start": stamp(appointment.window_start),
        "window_end": stamp(appointment.window_end),
        "scheduled_start": stamp(appointment.scheduled_start),
        "duration_minutes": appointment.duration_minutes,
        "gate_in_at": stamp(appointment.gate_in_at),
        "docked_at": stamp(appointment.docked_at),
        "done_at": stamp(appointment.done_at),
        "gate_out_at": stamp(appointment.gate_out_at),
    }


async def schedule(session: AsyncSession, turn_minutes: float) -> int:
    planned = (
        await session.scalars(
            select(Appointment)
            .where(Appointment.status == "planned")
            .order_by(Appointment.id)
            .with_for_update()
        )
    ).all()
    total = 0
    for direction, kind in (("inbound", "door_in"), ("outbound", "door_out")):
        doors = await pairs(session, select(Location.code, Location.id).where(Location.kind == kind))
        group = {a.code: a for a in planned if a.direction == direction}
        if not group or not doors:
            continue
        slots = [Slot(code, a.window_start, occupancy(a)) for code, a in group.items()]
        for code, (door, start) in assign_doors(slots, sorted(doors), turn_minutes).items():
            group[code].door_id = doors[door]
            group[code].scheduled_start = start
        total += len(group)
    return total


async def load(session: AsyncSession, code: str) -> Appointment:
    appointment = await session.scalar(select(Appointment).where(Appointment.code == code).with_for_update())
    if appointment is None:
        raise HTTPException(404, f"appointment {code} not found")
    return appointment


def expect(appointment: Appointment, status: str) -> None:
    if appointment.status != status:
        raise HTTPException(409, f"appointment {appointment.code} is {appointment.status}, expected {status}")


@router.post("/appointments", status_code=201)
async def create_appointment(body: AppointmentIn, sessions: SessionsDep) -> dict[str, Any]:
    if body.window_end <= body.window_start:
        raise HTTPException(422, "window_end must be after window_start")
    async with sessions() as session, session.begin():
        if await session.scalar(select(Appointment.id).where(Appointment.code == body.code)) is not None:
            raise HTTPException(409, f"appointment {body.code} already exists")
        appointment = Appointment(**body.model_dump())
        session.add(appointment)
        await session.flush()
        return await view(session, appointment)


@router.get("/appointments")
async def list_appointments(sessions: SessionsDep, direction: str | None = None) -> list[dict[str, Any]]:
    async with sessions() as session:
        query = select(Appointment).order_by(Appointment.window_start, Appointment.code)
        if direction is not None:
            query = query.where(Appointment.direction == direction)
        return [await view(session, a) for a in (await session.scalars(query)).all()]


@router.post("/yard/schedule")
async def schedule_yard(body: At, sessions: SessionsDep, settings: SettingsDep) -> dict[str, Any]:
    async with sessions() as session, session.begin():
        count = await schedule(session, settings.schedule.door_turn_minutes)
        log(session, body.at or now(), "yard.scheduled", "doors", appointments=count)
    return {"scheduled": count}


@router.post("/appointments/{code}/arrive")
async def arrive(code: str, body: At, sessions: SessionsDep) -> dict[str, Any]:
    at = body.at or now()
    async with sessions() as session, session.begin():
        appointment = await load(session, code)
        expect(appointment, "planned")
        appointment.status = "arrived"
        appointment.gate_in_at = at
        log(session, at, "gate.in", code)
        return await view(session, appointment)


@router.post("/appointments/{code}/dock")
async def dock(code: str, body: DockIn, sessions: SessionsDep) -> dict[str, Any]:
    at = body.at or now()
    async with sessions() as session, session.begin():
        appointment = await load(session, code)
        expect(appointment, "arrived")
        door_id = appointment.door_id
        if body.door is not None:
            door_id = await session.scalar(select(Location.id).where(Location.code == body.door))
        if door_id is None:
            raise HTTPException(409, f"appointment {code} has no door")
        door = await session.get_one(Location, door_id, with_for_update=True)
        wanted = "door_in" if appointment.direction == "inbound" else "door_out"
        if door.kind != wanted:
            raise HTTPException(409, f"{door.code} is not an {appointment.direction} door")
        busy = await session.scalar(
            select(Appointment.code).where(Appointment.door_id == door_id, Appointment.status == "docked")
        )
        if busy is not None:
            raise HTTPException(409, f"door {door.code} is busy with {busy}")
        appointment.door_id = door_id
        appointment.status = "docked"
        appointment.docked_at = at
        log(session, at, "door.docked", code, door=door.code)
        return await view(session, appointment)


@router.post("/appointments/{code}/finish")
async def finish(code: str, body: At, sessions: SessionsDep) -> dict[str, Any]:
    at = body.at or now()
    async with sessions() as session, session.begin():
        appointment = await load(session, code)
        expect(appointment, "docked")
        appointment.status = "done"
        appointment.done_at = at
        log(session, at, "door.done", code)
        return await view(session, appointment)


@router.post("/appointments/{code}/depart")
async def depart(code: str, body: At, sessions: SessionsDep) -> dict[str, Any]:
    at = body.at or now()
    async with sessions() as session, session.begin():
        appointment = await load(session, code)
        expect(appointment, "done")
        appointment.status = "departed"
        appointment.gate_out_at = at
        log(session, at, "gate.out", code)
        return await view(session, appointment)
