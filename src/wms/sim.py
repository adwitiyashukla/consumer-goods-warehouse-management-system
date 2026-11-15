from __future__ import annotations

import heapq
import itertools
import random
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

import httpx
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select, text

from wms.app import create_app
from wms.config import Settings
from wms.db import make_engine, make_sessions, wipe
from wms.generator import OrderPlan, Plan, seed
from wms.layout import Layout, Point
from wms.models import Location

DEMO_LOCK = 73105


class SimError(RuntimeError):
    pass


@dataclass(order=True)
class Event:
    at: datetime
    seq: int
    action: Callable[[], Awaitable[None]] = field(compare=False)


class Sim:
    def __init__(
        self, client: httpx.AsyncClient, settings: Settings, plan: Plan, until: datetime | None = None
    ) -> None:
        self.client = client
        self.settings = settings
        self.plan = plan
        self.layout = Layout(settings.layout)
        self.rng = random.Random(settings.sim.seed + 1)
        self.end = min(until, plan.end) if until else plan.end
        self.now = plan.start
        self.queue: list[Event] = []
        self.counter = itertools.count()
        self.generation: dict[str, int] = {}
        self.idle: dict[str, set[str]] = {"picker": set(), "driver": set()}
        self.position: dict[str, tuple[float, float]] = {}
        self.door_of: dict[str, str] = {}
        self.holder: dict[str, str | None] = {}
        self.waiting: dict[str, deque[str]] = {}
        self.unload: dict[str, tuple[str, float]] = {}
        self.departed: set[str] = set()
        self.trailer_of: dict[str, str] = {}
        self.calls = 0
        self.seconds = 0.0

    def later(self, at: datetime, action: Callable[[], Awaitable[None]]) -> None:
        heapq.heappush(self.queue, Event(at, next(self.counter), action))

    async def post(
        self, path: str, body: dict[str, Any], allow: tuple[int, ...] = ()
    ) -> dict[str, Any] | None:
        payload = jsonable_encoder(body)
        started = time.perf_counter()
        response = await self.client.post(path, json=payload)
        self.seconds += time.perf_counter() - started
        self.calls += 1
        if response.status_code in allow:
            return None
        if response.status_code >= 400:
            raise SimError(
                f"POST {path} at {self.now:%H:%M} returned {response.status_code}: {response.text}"
            )
        result: dict[str, Any] = response.json()
        return result

    async def act(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        result = await self.post(path, {**(body or {}), "at": self.now})
        assert result is not None
        return result

    async def get(self, path: str) -> Any:
        response = await self.client.get(path)
        if response.status_code >= 400:
            raise SimError(f"GET {path} returned {response.status_code}: {response.text}")
        return response.json()

    async def setup(self) -> None:
        sim = self.settings.sim
        for trailer in self.plan.trailers:
            await self.post("/api/appointments", trailer)
        for truck in self.plan.trucks:
            await self.post("/api/appointments", truck.appointment)
            await self.act("/api/asns", truck.asn)
            self.unload[truck.appointment["code"]] = (
                truck.asn["code"],
                truck.appointment["duration_minutes"],
            )
        await self.act("/api/yard/schedule")
        booked = {a["code"]: a for a in await self.get("/api/appointments")}
        for code, appointment in booked.items():
            self.door_of[code] = appointment["door"]
            self.holder.setdefault(appointment["door"], None)
            self.waiting.setdefault(appointment["door"], deque())
        for order in self.plan.orders:
            self.trailer_of[order.code] = order.appointment
            self.later(order.arrive_at, partial(self.new_order, order))
        lead = timedelta(minutes=self.settings.schedule.wave_lead_minutes)
        for trailer in self.plan.trailers:
            code = trailer["code"]
            leave = trailer["window_end"]
            arrive = min(
                datetime.fromisoformat(booked[code]["scheduled_start"]), leave - timedelta(minutes=1)
            )
            self.later(leave - lead, partial(self.release, code))
            self.later(arrive, partial(self.arrive, code))
            self.later(leave, partial(self.leave, code))
        for truck in self.plan.trucks:
            self.later(max(truck.arrive_at, self.plan.start), partial(self.arrive, truck.appointment["code"]))
        tick = self.plan.start
        while tick < self.plan.end:
            self.later(tick, self.replenish)
            tick += timedelta(minutes=sim.replen_every_minutes)
        if sim.count_tasks:
            self.later(self.plan.start + (self.plan.end - self.plan.start) / 2, self.counts)
        depot = (self.layout.x(self.layout.depot.aisle), 0.0)
        for role, count, prefix in (("picker", sim.pickers, "P"), ("driver", sim.drivers, "D")):
            for number in range(1, count + 1):
                name = f"{prefix}{number:02d}"
                self.generation[name] = 0
                self.position[name] = depot
                worker = self.picker if role == "picker" else self.driver
                self.later(self.plan.start, partial(worker, name, 0))

    async def run(self) -> dict[str, Any]:
        await self.setup()
        while self.queue and self.queue[0].at <= self.end:
            event = heapq.heappop(self.queue)
            self.now = max(self.now, event.at)
            await event.action()
        self.now = self.end
        return await self.report()

    async def plan_ahead(self) -> None:
        for trailer in self.plan.trailers:
            if trailer["window_end"] > self.end:
                await self.post("/api/waves", {"appointment": trailer["code"], "at": self.end}, (409,))

    def wake(self, role: str) -> None:
        for name in sorted(self.idle[role]):
            self.generation[name] += 1
            worker = self.picker if role == "picker" else self.driver
            self.later(self.now, partial(worker, name, self.generation[name]))
        self.idle[role].clear()

    def rest(self, role: str, name: str) -> None:
        self.idle[role].add(name)
        self.generation[name] += 1
        worker = self.picker if role == "picker" else self.driver
        poll = timedelta(minutes=self.settings.sim.poll_minutes)
        self.later(self.now + poll, partial(worker, name, self.generation[name]))

    async def new_order(self, order: OrderPlan) -> None:
        await self.act(
            "/api/orders",
            {
                "code": order.code,
                "customer": order.customer,
                "appointment": order.appointment,
                "ship_by": order.ship_by,
                "lines": [{"sku": sku, "cases": cases} for sku, cases in order.lines],
            },
        )

    async def release(self, trailer: str) -> None:
        result = await self.post(
            "/api/waves", {"appointment": trailer, "release": True, "at": self.now}, (409,)
        )
        if result is not None:
            self.wake("picker")
            self.wake("driver")

    async def picker(self, name: str, generation: int) -> None:
        if generation != self.generation[name]:
            return
        self.idle["picker"].discard(name)
        tour = (await self.act("/api/tasks/next", {"worker": name, "role": "picker"}))["tour"]
        if tour is None:
            self.rest("picker", name)
            return
        picking = self.settings.picking
        stops: list[tuple[Point, list[dict[str, Any]]]] = []
        for task in tour["tasks"]:
            point = Point(task["aisle"], task["y"])
            if stops and stops[-1][0] == point:
                stops[-1][1].append(task)
            else:
                stops.append((point, [task]))
        legs = []
        previous = self.layout.depot
        for point, _ in stops:
            legs.append(self.layout.distance(previous, point))
            previous = point
        back = self.layout.distance(previous, self.layout.depot)
        direct = sum(legs) + back
        scale = tour["distance_m"] / direct if direct > 0 else 1.0
        at = self.now
        for (_, group), leg in zip(stops, legs, strict=True):
            at += timedelta(seconds=leg * scale / picking.walk_speed_mps + picking.seconds_per_stop)
            for task in group:
                at += timedelta(seconds=picking.seconds_per_case * task["cases"])
                self.later(at, partial(self.pick, name, task["id"], task["cases"]))
        at += timedelta(seconds=back * scale / picking.walk_speed_mps)
        self.later(at, partial(self.picker, name, generation))

    async def pick(self, name: str, task: int, cases: int) -> None:
        result = await self.act(f"/api/tasks/{task}/complete", {"worker": name, "cases": cases})
        if result["order_picked"]:
            await self.picked(result["order"])

    async def picked(self, order: str) -> None:
        if self.trailer_of.get(order) in self.departed:
            await self.act(f"/api/orders/{order}/ship")

    async def driver(self, name: str, generation: int) -> None:
        if generation != self.generation[name]:
            return
        self.idle["driver"].discard(name)
        task = (await self.act("/api/tasks/next", {"worker": name, "role": "driver"}))["task"]
        if task is None:
            self.rest("driver", name)
            return
        forklift = self.settings.forklift
        source = (task["from"]["x"], task["from"]["y"])
        target = (task["to"]["x"], task["to"]["y"])
        meters = self.layout.travel(*self.position[name], *source) + self.layout.travel(*source, *target)
        done = self.now + timedelta(seconds=meters / forklift.speed_mps + forklift.seconds_per_move)
        self.later(done, partial(self.drop, name, generation, task, target))

    async def drop(
        self, name: str, generation: int, task: dict[str, Any], target: tuple[float, float]
    ) -> None:
        body: dict[str, Any] = {"worker": name}
        if task["kind"] == "count":
            miss = self.rng.random() < self.settings.sim.count_error_rate
            body["counted"] = max(0, task["cases"] - (1 if miss else 0))
        result = await self.act(f"/api/tasks/{task['id']}/complete", body)
        self.position[name] = target
        if task["kind"] == "replen" and result.get("unblocked"):
            self.wake("picker")
        if task["kind"] == "pallet" and result.get("order_picked"):
            await self.picked(result["order"])
        await self.driver(name, generation)

    async def arrive(self, code: str) -> None:
        await self.act(f"/api/appointments/{code}/arrive")
        door = self.door_of[code]
        if self.holder[door] is None:
            await self.dock(code)
        else:
            self.waiting[door].append(code)

    async def dock(self, code: str) -> None:
        self.holder[self.door_of[code]] = code
        await self.act(f"/api/appointments/{code}/dock")
        if code in self.unload:
            minutes = self.unload[code][1]
            self.later(self.now + timedelta(minutes=minutes), partial(self.unloaded, code))

    async def unloaded(self, code: str) -> None:
        await self.act(f"/api/asns/{self.unload[code][0]}/receive")
        await self.close_door(code)
        self.wake("driver")

    async def close_door(self, code: str) -> None:
        await self.act(f"/api/appointments/{code}/finish")
        await self.act(f"/api/appointments/{code}/depart")
        door = self.door_of[code]
        self.holder[door] = None
        if self.waiting[door]:
            nxt = self.waiting[door].popleft()
            self.holder[door] = nxt
            turn = timedelta(minutes=self.settings.schedule.door_turn_minutes)
            self.later(self.now + turn, partial(self.dock, nxt))

    async def leave(self, code: str) -> None:
        result = await self.act(f"/api/appointments/{code}/ship")
        self.departed.add(code)
        if self.holder[self.door_of[code]] == code:
            await self.close_door(code)
        for order in result["left"]:
            if order["status"] == "picked":
                await self.act(f"/api/orders/{order['code']}/ship")

    async def replenish(self) -> None:
        if (await self.act("/api/replenishment/run"))["created"]:
            self.wake("driver")

    async def counts(self) -> None:
        await self.act(
            "/api/counts", {"count": self.settings.sim.count_tasks, "seed": self.settings.sim.seed}
        )
        self.wake("driver")

    async def report(self) -> dict[str, Any]:
        kpis = await self.get("/api/kpis")
        return {
            "until": self.now.isoformat(),
            "calls": self.calls,
            "api_ms_avg": round(1000 * self.seconds / max(self.calls, 1), 2),
            "kpis": kpis,
        }


async def simulate(
    settings: Settings, until: datetime | None = None, url: str | None = None, ahead: bool = False
) -> dict[str, Any]:
    engine = make_engine(settings.database, pooled=False)
    try:
        await wipe(engine)
        async with make_sessions(engine)() as session, session.begin():
            plan = await seed(session, settings)
    finally:
        await engine.dispose()
    if url is not None:
        async with httpx.AsyncClient(base_url=url, timeout=120) as client:
            return await drive(Sim(client, settings, plan, until), ahead)
    app = create_app(settings)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://wms", timeout=120) as client:
            return await drive(Sim(client, settings, plan, until), ahead)
    finally:
        await app.state.engine.dispose()


async def drive(sim: Sim, ahead: bool) -> dict[str, Any]:
    report = await sim.run()
    if ahead:
        await sim.plan_ahead()
    return report


def summary(report: dict[str, Any]) -> str:
    kpis = report["kpis"]
    picking = kpis["picking"]
    lines = [
        f"simulated until   {report['until']}",
        f"api calls         {report['calls']} ({report['api_ms_avg']} ms average)",
        f"orders shipped    {kpis['shipped']['orders']} of {kpis['orders']['total']}",
        f"otif              {kpis['shipped']['otif']}",
        f"tours done        {picking['tours_done']}",
        f"walked            {picking['distance_m'] / 1000:.2f} km in {picking['picker_hours']} picker hours",
        f"short cases       {kpis['lines']['short']}",
        f"inbound dwell     {kpis['inbound']['dwell_avg_min']} min average",
        f"oversold pallets  {kpis['stock']['oversold_pallets']}",
    ]
    return "\n".join(lines)


async def demo(settings: Settings, fresh: bool) -> None:
    engine = make_engine(settings.database, pooled=False)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("select pg_advisory_lock(:key)"), {"key": DEMO_LOCK})
            try:
                empty = (await conn.scalar(select(func.count(Location.id)))) == 0
                await conn.commit()
                if fresh or empty:
                    stop = datetime.combine(settings.schedule.date, settings.server.demo_until, tzinfo=UTC)
                    print(summary(await simulate(settings, until=stop, ahead=True)), flush=True)
            finally:
                await conn.execute(text("select pg_advisory_unlock(:key)"), {"key": DEMO_LOCK})
                await conn.commit()
    finally:
        await engine.dispose()
