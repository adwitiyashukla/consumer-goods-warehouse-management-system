from datetime import UTC, date, datetime, timedelta

import httpx
from fastapi import FastAPI
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import select

from wms.allocation import Candidate, plan_line
from wms.generator import Plan
from wms.models import Customer, Location, Lot, Sku, Stock

DAY = date(2026, 3, 2)


@st.composite
def stock(draw: st.DrawFn) -> list[Candidate]:
    rows = []
    for number in range(draw(st.integers(0, 8))):
        on_hand = draw(st.integers(1, 60))
        rows.append(
            Candidate(
                stock_id=number + 1,
                in_reserve=draw(st.booleans()),
                expires_on=DAY + timedelta(days=draw(st.integers(0, 30))),
                on_hand=on_hand,
                allocated=draw(st.integers(0, on_hand)),
            )
        )
    return rows


@settings(max_examples=400, deadline=None)
@given(st.integers(1, 200), st.integers(5, 40), stock(), st.booleans())
def test_plan_line_rules(need: int, per_pallet: int, rows: list[Candidate], full: bool) -> None:
    takes, short = plan_line(need, per_pallet, rows, full)
    by_id = {row.stock_id: row for row in rows}
    used: dict[int, int] = {}
    for take in takes:
        used[take.stock_id] = used.get(take.stock_id, 0) + take.cases
        if take.kind == "pallet":
            row = by_id[take.stock_id]
            assert full and row.in_reserve and row.allocated == 0 and row.on_hand == per_pallet
    assert sum(used.values()) + short == need
    assert all(used[i] <= by_id[i].available for i in used)
    available = sum(row.available for row in rows)
    assert short == max(0, need - available)
    for take in takes:
        if take.kind != "case":
            continue
        row = by_id[take.stock_id]
        for other in rows:
            older = (other.expires_on, other.stock_id) < (row.expires_on, row.stock_id)
            if older and other.in_reserve == row.in_reserve:
                assert used.get(other.stock_id, 0) == other.available


def test_pick_slot_is_used_before_reserve() -> None:
    rows = [
        Candidate(1, True, DAY, 40, 0),
        Candidate(2, False, DAY + timedelta(days=5), 10, 0),
    ]
    takes, short = plan_line(12, 40, rows, full_pallets=True)
    assert [(t.stock_id, t.cases) for t in takes] == [(2, 10), (1, 2)]
    assert short == 0


async def order_body(
    client: httpx.AsyncClient, code: str, customer: str, sku: str, cases: int
) -> dict[str, object]:
    response = await client.post(
        "/api/orders",
        json={
            "code": code,
            "customer": customer,
            "ship_by": datetime(2026, 3, 2, 13, 0, tzinfo=UTC).isoformat(),
            "lines": [{"sku": sku, "cases": cases}],
        },
    )
    assert response.status_code == 201
    result: dict[str, object] = response.json()
    return result


async def test_shelf_life_rule_and_fefo(client: httpx.AsyncClient, app: FastAPI, plan: Plan) -> None:
    async with app.state.sessions() as session, session.begin():
        sku = await session.scalar(select(Sku).order_by(Sku.id))
        strict = await session.scalar(select(Customer).order_by(Customer.id))
        easy = await session.scalar(select(Customer).order_by(Customer.id.desc()))
        strict.min_shelf_life_days = 200
        easy.min_shelf_life_days = 10
        await session.execute(Stock.__table__.delete().where(Stock.sku_id == sku.id))
        old = Lot(
            sku_id=sku.id,
            code="OLD",
            produced_on=DAY - timedelta(days=300),
            expires_on=DAY + timedelta(days=60),
        )
        new = Lot(
            sku_id=sku.id,
            code="NEW",
            produced_on=DAY - timedelta(days=5),
            expires_on=DAY + timedelta(days=360),
        )
        session.add_all([old, new])
        await session.flush()
        slot = await session.get(Location, sku.pick_location_id)
        session.add_all(
            [
                Stock(lpn="T-OLD", sku_id=sku.id, lot_id=old.id, location_id=slot.id, cases_on_hand=10),
                Stock(lpn="T-NEW", sku_id=sku.id, lot_id=new.id, location_id=slot.id, cases_on_hand=10),
            ]
        )
        codes = (sku.code, strict.code, easy.code)
    sku_code, strict_code, easy_code = codes
    strict_order = await order_body(client, "SO-STRICT", strict_code, sku_code, 15)
    assert strict_order["allocated"] == 10
    assert strict_order["short"] == 5
    detail = (await client.get(f"/api/skus/{sku_code}")).json()
    allocated = {row["lot"]: row["cases_allocated"] for row in detail["stock"]}
    assert allocated == {"OLD": 0, "NEW": 10}
    easy_order = await order_body(client, "SO-EASY", easy_code, sku_code, 4)
    assert easy_order["short"] == 0
    detail = (await client.get(f"/api/skus/{sku_code}")).json()
    assert {row["lot"]: row["cases_allocated"] for row in detail["stock"]} == {"OLD": 4, "NEW": 10}
