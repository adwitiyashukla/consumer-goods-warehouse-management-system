import asyncio
from datetime import UTC, datetime

import httpx
from fastapi import FastAPI
from sqlalchemy import func, select

from wms.generator import Plan
from wms.models import Allocation, Customer, Location, OrderLine, Sku, Stock

SHIP_BY = datetime(2026, 3, 2, 13, 0, tzinfo=UTC).isoformat()


async def available(app: FastAPI, sku: str) -> int:
    async with app.state.sessions() as session:
        total = await session.scalar(
            select(func.sum(Stock.cases_on_hand - Stock.cases_allocated))
            .join(Sku, Stock.sku_id == Sku.id)
            .join(Location, Stock.location_id == Location.id)
            .where(Sku.code == sku, Stock.status == "available", Location.kind.in_(("pick", "reserve")))
        )
        return int(total or 0)


async def test_parallel_orders_never_oversell(client: httpx.AsyncClient, app: FastAPI, plan: Plan) -> None:
    sku = max(plan.popularity, key=plan.popularity.__getitem__)
    customer = plan.orders[0].customer
    async with app.state.sessions() as session, session.begin():
        row = await session.scalar(select(Customer).where(Customer.code == customer))
        row.min_shelf_life_days = 0
    before = await available(app, sku)
    sizes = [7 + (i * 5) % 23 for i in range(40)]

    async def place(number: int, cases: int) -> httpx.Response:
        body = {"code": f"RUSH{number:03d}", "customer": customer, "ship_by": SHIP_BY}
        return await client.post("/api/orders", json={**body, "lines": [{"sku": sku, "cases": cases}]})

    responses = await asyncio.gather(*(place(i, size) for i, size in enumerate(sizes)))
    assert [r.status_code for r in responses] == [201] * len(sizes)
    allocated = sum(r.json()["allocated"] for r in responses)
    assert allocated == min(before, sum(sizes))
    assert await available(app, sku) == before - allocated
    async with app.state.sessions() as session:
        bad = await session.scalar(
            select(func.count(Stock.id)).where(Stock.cases_allocated > Stock.cases_on_hand)
        )
        assert bad == 0
        lines = await session.scalar(select(func.sum(OrderLine.cases_allocated)))
        rows = await session.scalar(select(func.sum(Allocation.cases)))
        assert lines == rows == allocated


async def test_crossed_lines_do_not_deadlock(client: httpx.AsyncClient, plan: Plan) -> None:
    top = sorted(plan.popularity, key=plan.popularity.__getitem__, reverse=True)[:2]
    customer = plan.orders[0].customer

    async def place(number: int) -> httpx.Response:
        first, second = top if number % 2 else top[::-1]
        lines = [{"sku": first, "cases": 2}, {"sku": second, "cases": 2}]
        body = {"code": f"X{number:03d}", "customer": customer, "ship_by": SHIP_BY, "lines": lines}
        return await client.post("/api/orders", json=body)

    responses = await asyncio.gather(*(place(i) for i in range(30)))
    assert all(r.status_code == 201 for r in responses)


async def test_pickers_never_share_a_tour(client: httpx.AsyncClient, plan: Plan) -> None:
    for trailer in plan.trailers:
        body = {
            **trailer,
            "window_start": trailer["window_start"].isoformat(),
            "window_end": trailer["window_end"].isoformat(),
        }
        await client.post("/api/appointments", json=body)
    trailer = plan.orders[0].appointment
    for o in plan.orders:
        if o.appointment == trailer:
            lines = [{"sku": sku, "cases": cases} for sku, cases in o.lines]
            body = {
                "code": o.code,
                "customer": o.customer,
                "appointment": trailer,
                "ship_by": o.ship_by.isoformat(),
            }
            await client.post("/api/orders", json={**body, "lines": lines})
    await client.post("/api/waves", json={"appointment": trailer, "release": True})
    for _ in range(40):
        task = (await client.post("/api/tasks/next", json={"worker": "D01", "role": "driver"})).json()["task"]
        if task is None:
            break
        await client.post(
            f"/api/tasks/{task['id']}/complete", json={"worker": "D01", "counted": task["cases"]}
        )
    claims = await asyncio.gather(
        *(client.post("/api/tasks/next", json={"worker": f"P{i:02d}", "role": "picker"}) for i in range(8))
    )
    tours = [c.json()["tour"]["id"] for c in claims if c.json()["tour"] is not None]
    assert tours
    assert len(tours) == len(set(tours))
