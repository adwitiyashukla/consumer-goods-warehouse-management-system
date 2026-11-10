from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from dockhand.db import log
from dockhand.models import Allocation, Customer, Location, Lot, Order, OrderLine, Sku, Stock


@dataclass(frozen=True)
class Candidate:
    stock_id: int
    in_reserve: bool
    expires_on: date
    on_hand: int
    allocated: int

    @property
    def available(self) -> int:
        return self.on_hand - self.allocated


@dataclass(frozen=True)
class Take:
    stock_id: int
    cases: int
    kind: str


def plan_line(
    need: int, cases_per_pallet: int, candidates: Sequence[Candidate], full_pallets: bool
) -> tuple[list[Take], int]:
    ordered = sorted(candidates, key=lambda c: (c.expires_on, c.stock_id))
    left = {c.stock_id: c.available for c in ordered}
    takes: list[Take] = []
    remaining = need
    if full_pallets:
        for c in ordered:
            if remaining < cases_per_pallet:
                break
            if c.in_reserve and c.allocated == 0 and c.on_hand == cases_per_pallet:
                takes.append(Take(c.stock_id, cases_per_pallet, "pallet"))
                left[c.stock_id] = 0
                remaining -= cases_per_pallet
    for in_reserve in (False, True):
        for c in ordered:
            if remaining == 0:
                break
            if c.in_reserve != in_reserve or left[c.stock_id] <= 0:
                continue
            cases = min(remaining, left[c.stock_id])
            takes.append(Take(c.stock_id, cases, "case"))
            left[c.stock_id] -= cases
            remaining -= cases
    return takes, remaining


def earliest_expiry(ship_by: datetime, min_shelf_life_days: int) -> date:
    return ship_by.date() + timedelta(days=min_shelf_life_days)


async def allocate(session: AsyncSession, order: Order, full_pallets: bool, at: datetime) -> int:
    customer = await session.get_one(Customer, order.customer_id)
    floor = earliest_expiry(order.ship_by, customer.min_shelf_life_days)
    lines = (
        await session.execute(
            select(OrderLine, Sku.cases_per_pallet)
            .join(Sku, OrderLine.sku_id == Sku.id)
            .where(OrderLine.order_id == order.id)
            .order_by(OrderLine.sku_id, OrderLine.id)
            .with_for_update(of=OrderLine)
        )
    ).all()
    short = 0
    for line, cases_per_pallet in lines:
        need = line.cases_ordered - line.cases_allocated
        if need <= 0:
            continue
        rows = (
            await session.execute(
                select(
                    Stock.id,
                    Stock.cases_on_hand,
                    Stock.cases_allocated,
                    Lot.expires_on,
                    Location.kind,
                )
                .join(Lot, Stock.lot_id == Lot.id)
                .join(Location, Stock.location_id == Location.id)
                .where(
                    Stock.sku_id == line.sku_id,
                    Stock.status == "available",
                    Location.kind.in_(("pick", "reserve")),
                    Lot.expires_on >= floor,
                )
                .order_by(Lot.expires_on, Stock.id)
                .with_for_update(of=Stock)
            )
        ).all()
        candidates = [
            Candidate(row.id, row.kind == "reserve", row.expires_on, row.cases_on_hand, row.cases_allocated)
            for row in rows
        ]
        takes, missing = plan_line(need, cases_per_pallet, candidates, full_pallets)
        for take in takes:
            await session.execute(
                update(Stock)
                .where(Stock.id == take.stock_id)
                .values(cases_allocated=Stock.cases_allocated + take.cases)
            )
            session.add(
                Allocation(order_line_id=line.id, stock_id=take.stock_id, cases=take.cases, kind=take.kind)
            )
        line.cases_allocated += need - missing
        short += missing
    order.status = "allocated"
    log(session, at, "order.allocated", order.code, short=short)
    return short
