from datetime import timedelta
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select

from wms.generator import Plan
from wms.models import Location, Stock, Task


async def post(client: httpx.AsyncClient, path: str, body: dict[str, Any], status: int = 200) -> Any:
    response = await client.post(path, json=jsonable_encoder(body))
    assert response.status_code == status, response.text
    return response.json()


async def book(client: httpx.AsyncClient, plan: Plan) -> None:
    for trailer in plan.trailers:
        await post(client, "/api/appointments", trailer, 201)
    for truck in plan.trucks:
        await post(client, "/api/appointments", truck.appointment, 201)
        await post(client, "/api/asns", truck.asn, 201)
    await post(client, "/api/yard/schedule", {})


async def order(client: httpx.AsyncClient, plan: Plan, index: int) -> dict[str, Any]:
    o = plan.orders[index]
    body = {
        "code": o.code,
        "customer": o.customer,
        "appointment": o.appointment,
        "ship_by": o.ship_by,
        "at": o.arrive_at,
        "lines": [{"sku": sku, "cases": cases} for sku, cases in o.lines],
    }
    result: dict[str, Any] = await post(client, "/api/orders", body, 201)
    return result


async def test_health(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/health/live")).json() == {"status": "ok"}
    assert (await client.get("/api/health/ready")).json() == {"status": "ready"}


async def test_order_to_trailer(client: httpx.AsyncClient, plan: Plan) -> None:
    await book(client, plan)
    trailer = plan.orders[0].appointment
    mine = [i for i, o in enumerate(plan.orders) if o.appointment == trailer]
    for index in mine:
        created = await order(client, plan, index)
        assert created["status"] == "allocated"
    leave = plan.orders[mine[0]].ship_by
    at = leave - timedelta(hours=2)
    wave = await post(client, "/api/waves", {"appointment": trailer, "release": True, "at": at}, 201)
    assert wave["status"] == "released" and wave["orders"] == len(mine)
    await post(client, "/api/waves", {"appointment": trailer, "at": at}, 409)
    for _ in range(50):
        work = await post(client, "/api/tasks/next", {"worker": "D01", "role": "driver", "at": at})
        if work["task"] is None:
            break
        task = work["task"]
        body = {"worker": "D01", "at": at}
        if task["kind"] == "count":
            body["counted"] = task["cases"]
        await post(client, f"/api/tasks/{task['id']}/complete", body)
    tours = 0
    while True:
        got = (await post(client, "/api/tasks/next", {"worker": "P01", "role": "picker", "at": at}))["tour"]
        if got is None:
            break
        tours += 1
        for task in got["tasks"]:
            done = await post(client, f"/api/tasks/{task['id']}/complete", {"worker": "P01", "at": at})
            assert done["cases"] == task["cases"]
    assert tours == wave["tours"]
    shipped = await post(client, f"/api/appointments/{trailer}/ship", {"at": leave})
    assert sorted(shipped["shipped"]) == sorted(plan.orders[i].code for i in mine)
    kpis = (await client.get("/api/kpis")).json()
    assert kpis["shipped"]["orders"] == len(mine)
    assert kpis["shipped"]["on_time"] == 1.0
    assert kpis["stock"]["oversold_pallets"] == 0
    routes = (await client.get("/api/tours")).json()
    compare = (await client.get(f"/api/tours/{routes[0]['id']}/routes")).json()
    assert compare["routes"]["optimal"]["distance_m"] <= compare["routes"]["sshape"]["distance_m"]


async def test_receive_and_putaway(client: httpx.AsyncClient, plan: Plan, app: FastAPI) -> None:
    await book(client, plan)
    truck = plan.trucks[0]
    code = truck.appointment["code"]
    at = truck.arrive_at
    await post(client, f"/api/appointments/{code}/arrive", {"at": at})
    await post(client, f"/api/appointments/{code}/dock", {"at": at})
    received = await post(client, f"/api/asns/{truck.asn['code']}/receive", {"at": at})
    assert received["pallets"] >= 1
    await post(client, f"/api/asns/{truck.asn['code']}/receive", {"at": at}, 409)
    for _ in range(received["pallets"]):
        task = (await post(client, "/api/tasks/next", {"worker": "D01", "role": "driver", "at": at}))["task"]
        assert task["kind"] in ("putaway", "count", "replen")
        await post(
            client, f"/api/tasks/{task['id']}/complete", {"worker": "D01", "at": at, "counted": task["cases"]}
        )
    async with app.state.sessions() as session:
        waiting = await session.scalar(
            select(func.count(Stock.id)).join(Location).where(Location.kind == "door_in")
        )
        assert waiting == 0


async def test_scanner_retries_are_safe(client: httpx.AsyncClient, plan: Plan, app: FastAPI) -> None:
    await book(client, plan)
    truck = plan.trucks[0]
    await post(client, f"/api/asns/{truck.asn['code']}/receive", {"at": truck.arrive_at})
    async with app.state.sessions() as session:
        task_id = await session.scalar(select(Task.id).where(Task.kind == "putaway").order_by(Task.id))
    body = {"worker": "D01", "at": truck.arrive_at}
    headers = {"Idempotency-Key": "scan-1"}
    first = await client.post(f"/api/tasks/{task_id}/complete", json=jsonable_encoder(body), headers=headers)
    again = await client.post(f"/api/tasks/{task_id}/complete", json=jsonable_encoder(body), headers=headers)
    assert first.status_code == again.status_code == 200
    assert first.json() == again.json()
    other = {**body, "worker": "D02"}
    clash = await client.post(f"/api/tasks/{task_id}/complete", json=jsonable_encoder(other), headers=headers)
    assert clash.status_code == 422
    plain = await client.post(f"/api/tasks/{task_id}/complete", json=jsonable_encoder(body))
    assert plain.status_code == 409


async def test_bad_requests(client: httpx.AsyncClient, plan: Plan) -> None:
    o = plan.orders[0]
    base = {
        "code": "X1",
        "customer": o.customer,
        "ship_by": o.ship_by,
        "lines": [{"sku": o.lines[0][0], "cases": 0}],
    }
    await post(client, "/api/orders", base, 422)
    await post(client, "/api/orders", {**base, "lines": [{"sku": "NOPE", "cases": 1}]}, 404)
    await post(
        client,
        "/api/orders",
        {**base, "customer": "NOPE", "lines": [{"sku": o.lines[0][0], "cases": 1}]},
        404,
    )
    naive = {**base, "lines": [{"sku": o.lines[0][0], "cases": 1}], "ship_by": "2026-03-02T10:00:00"}
    await post(client, "/api/orders", naive, 422)
    await post(client, "/api/tasks/999999/complete", {}, 404)
    await post(client, "/api/waves/NOPE/release", {}, 404)
