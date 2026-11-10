from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from dockhand.config import Settings
from dockhand.db import pairs
from dockhand.layout import Layout, Point
from dockhand.models import Customer, Location, Lot, Sku, Stock
from dockhand.slotting import assign, classes

PRODUCTS = {
    "laundry": ["Liquid detergent", "Laundry pods", "Fabric softener", "Stain remover", "Dryer sheets"],
    "dish": ["Dish soap", "Dishwasher pods", "Rinse aid"],
    "paper": ["Paper towels", "Bath tissue", "Facial tissue"],
    "baby": ["Diapers", "Baby wipes", "Training pants"],
    "hair": ["Shampoo", "Conditioner", "Styling gel"],
    "oral": ["Toothpaste", "Mouthwash", "Toothbrushes"],
    "skin": ["Body wash", "Bar soap", "Hand lotion"],
    "home": ["Surface cleaner", "Air freshener", "Trash bags"],
}
VARIANTS = ["original", "unscented", "fresh scent", "sensitive", "value pack", "family size", "travel size"]
CITIES = [
    "Columbus",
    "Dayton",
    "Louisville",
    "Indianapolis",
    "Lexington",
    "Pittsburgh",
    "Cleveland",
    "Toledo",
    "Detroit",
    "Chicago",
    "Nashville",
    "Charlotte",
    "Atlanta",
    "St Louis",
    "Memphis",
    "Knoxville",
    "Richmond",
    "Baltimore",
    "Philadelphia",
    "Buffalo",
    "Milwaukee",
    "Kansas City",
    "Omaha",
    "Des Moines",
    "Minneapolis",
    "Birmingham",
    "Jackson",
    "Little Rock",
    "Tulsa",
    "Dallas",
    "Houston",
    "Austin",
    "Denver",
    "Phoenix",
    "Raleigh",
    "Columbia",
]
PALLET_M3 = 1.2 * 1.0 * 1.4 * 0.85
LINE_SIGMA = 0.9


@dataclass
class OrderPlan:
    code: str
    customer: str
    appointment: str
    ship_by: datetime
    arrive_at: datetime
    lines: list[tuple[str, int]]


@dataclass
class TruckPlan:
    appointment: dict[str, Any]
    asn: dict[str, Any]
    arrive_at: datetime


@dataclass
class Plan:
    start: datetime
    end: datetime
    trailers: list[dict[str, Any]] = field(default_factory=list)
    trucks: list[TruckPlan] = field(default_factory=list)
    orders: list[OrderPlan] = field(default_factory=list)
    popularity: dict[str, float] = field(default_factory=dict)


def moment(day: date, clock: time, minutes: float = 0.0) -> datetime:
    return datetime.combine(day, clock, tzinfo=UTC) + timedelta(minutes=minutes)


def weighted_sample(rng: random.Random, items: list[str], weights: list[float], k: int) -> list[str]:
    keyed = sorted(
        ((math.log(1.0 - rng.random()) / w, item) for item, w in zip(items, weights, strict=True)),
        reverse=True,
    )
    return [item for _, item in keyed[:k]]


def line_cases(rng: random.Random, mean: float) -> int:
    mu = math.log(mean) - LINE_SIGMA**2 / 2
    return max(1, round(rng.lognormvariate(mu, LINE_SIGMA)))


def catalog(settings: Settings, rng: random.Random) -> list[dict[str, Any]]:
    cfg = settings.catalog
    names = list(PRODUCTS)
    rows = []
    for number in range(1, cfg.skus + 1):
        category = names[(number - 1) % len(names)]
        product = rng.choice(PRODUCTS[category])
        per_pallet = rng.randint(*cfg.cases_per_pallet)
        rows.append(
            {
                "code": f"SKU{number:05d}",
                "description": f"{product} {rng.choice(VARIANTS)} {rng.choice([4, 6, 8, 12])}ct",
                "category": category,
                "cases_per_pallet": per_pallet,
                "case_volume_m3": round(PALLET_M3 / per_pallet, 5),
                "case_weight_kg": round(rng.uniform(3.0, 16.0), 1),
                "shelf_life_days": rng.randint(*cfg.shelf_life_days),
            }
        )
    return rows


def locations(settings: Settings, layout: Layout) -> list[dict[str, Any]]:
    cfg = settings.layout
    rows: list[dict[str, Any]] = []
    for aisle in range(cfg.aisles):
        for bay in range(cfg.bays_per_aisle):
            for side in "LR":
                for level in range(1, cfg.levels + 1):
                    rows.append(
                        {
                            "code": f"{aisle + 1:02d}-{bay + 1:02d}-{side}{level}",
                            "kind": "pick" if level == 1 else "reserve",
                            "aisle": aisle,
                            "bay": bay,
                            "side": side,
                            "level": level,
                            "x": layout.x(aisle),
                            "y": layout.bay_y(bay),
                        }
                    )
    total = cfg.outbound_doors + cfg.inbound_doors
    for index in range(total):
        outbound = index < cfg.outbound_doors
        number = index + 1 if outbound else index - cfg.outbound_doors + 1
        rows.append(
            {
                "code": f"{'OUT' if outbound else 'IN'}{number:02d}",
                "kind": "door_out" if outbound else "door_in",
                "x": layout.door_x(index, total),
                "y": -cfg.dock_depth_m,
            }
        )
    rows.append({"code": "STAGE", "kind": "stage", "x": layout.x(cfg.depot_aisle), "y": 0.0})
    return rows


def daily_cases(settings: Settings, share: float) -> float:
    sim = settings.sim
    lines = sim.orders * (sim.lines_per_order[0] + sim.lines_per_order[1]) / 2
    return lines * share * sim.cases_per_line


async def seed(session: AsyncSession, settings: Settings) -> Plan:
    rng = random.Random(settings.sim.seed)
    layout = Layout(settings.layout)
    day = settings.schedule.date
    sku_rows = catalog(settings, rng)
    order = list(range(len(sku_rows)))
    rng.shuffle(order)
    skew = settings.catalog.popularity_skew
    raw = {index: 1.0 / (rank + 1) ** skew for rank, index in enumerate(order)}
    total = sum(raw.values())
    share = {index: weight / total for index, weight in raw.items()}
    await session.execute(insert(Location), locations(settings, layout))
    await session.execute(insert(Sku), sku_rows)
    await session.flush()
    places = {
        row.code: row
        for row in (
            await session.execute(Location.__table__.select().where(Location.kind.in_(("pick", "reserve"))))
        ).all()
    }
    pick_slots = sorted((row.id, Point(row.aisle, row.y)) for row in places.values() if row.kind == "pick")
    ids = await pairs(session, select(Sku.code, Sku.id))
    frequency = {ids[sku_rows[index]["code"]]: share[index] for index in range(len(sku_rows))}
    slotting = assign(layout, pick_slots, frequency, settings.slotting.policy, settings.sim.seed)
    labels = classes(frequency, settings.slotting.a_share, settings.slotting.b_share)
    for sku_id, slot_id in slotting.items():
        await session.execute(
            update(Sku).where(Sku.id == sku_id).values(pick_location_id=slot_id, velocity=labels[sku_id])
        )
    slot_point = {slot_id: point for slot_id, point in pick_slots}
    reserve = sorted((row.id, Point(row.aisle, row.y)) for row in places.values() if row.kind == "reserve")
    used: set[int] = set()
    lots: dict[tuple[int, str], dict[str, Any]] = {}
    pallets: list[tuple[int, tuple[int, str], int, int]] = []
    for index in sorted(range(len(sku_rows)), key=lambda i: -share[i]):
        row = sku_rows[index]
        sku_id = ids[row["code"]]
        per_pallet = row["cases_per_pallet"]
        need = settings.sim.initial_days_of_stock * daily_cases(settings, share[index])
        count = max(2, math.ceil(need / per_pallet))
        ages = sorted(
            (rng.randint(*settings.sim.lot_age_days) for _ in range(max(1, count // 3))), reverse=True
        )
        target = slot_point[slotting[sku_id]]
        nearby = iter(sorted(reserve, key=lambda item: (layout.distance(item[1], target), item[0])))
        for number in range(count):
            age = ages[min(number // 3, len(ages) - 1)]
            produced = day - timedelta(days=age)
            key = (sku_id, f"{produced:%y%m%d}-P{1 + (sku_id + age) % 4}")
            lots.setdefault(
                key,
                {
                    "sku_id": sku_id,
                    "code": key[1],
                    "produced_on": produced,
                    "expires_on": produced + timedelta(days=row["shelf_life_days"]),
                },
            )
            if number == 0:
                pallets.append((sku_id, key, slotting[sku_id], per_pallet))
                continue
            location = next((loc for loc, _ in nearby if loc not in used), None)
            if location is None:
                break
            used.add(location)
            pallets.append((sku_id, key, location, per_pallet))
    await session.execute(insert(Lot), list(lots.values()))
    lot_ids = {
        (row.sku_id, row.code): row.id for row in (await session.execute(Lot.__table__.select())).all()
    }
    stock_rows = [
        {
            "lpn": f"INIT-{number:06d}",
            "sku_id": sku_id,
            "lot_id": lot_ids[key],
            "location_id": location,
            "cases_on_hand": cases,
            "status": "available",
        }
        for number, (sku_id, key, location, cases) in enumerate(pallets, 1)
    ]
    await session.execute(insert(Stock), stock_rows)
    customers = []
    for number in range(1, settings.customers.count + 1):
        city = CITIES[(number - 1) % len(CITIES)]
        suffix = "" if number <= len(CITIES) else f" {number // len(CITIES) + 1}"
        customers.append(
            {
                "code": f"C{number:03d}",
                "name": f"{city} DC{suffix}",
                "lane": (number - 1) % settings.customers.lanes + 1,
                "min_shelf_life_days": rng.randint(*settings.customers.min_shelf_life_days),
            }
        )
    await session.execute(insert(Customer), customers)
    plan = Plan(
        start=moment(day, settings.sim.start),
        end=moment(day, settings.sim.end),
        popularity={row["code"]: share[index] for index, row in enumerate(sku_rows)},
    )
    plan_day(settings, rng, plan, sku_rows, share, customers)
    return plan


def plan_day(
    settings: Settings,
    rng: random.Random,
    plan: Plan,
    sku_rows: list[dict[str, Any]],
    share: dict[int, float],
    customers: list[dict[str, Any]],
) -> None:
    day = settings.schedule.date
    schedule = settings.schedule
    departures: dict[int, list[tuple[str, datetime]]] = {}
    for lane in range(1, settings.customers.lanes + 1):
        for number, clock in enumerate(schedule.departures, 1):
            leave = moment(day, clock, (lane - 1) * schedule.lane_stagger_minutes)
            code = f"OUT-L{lane}-{number}"
            departures.setdefault(lane, []).append((code, leave))
            plan.trailers.append(
                {
                    "code": code,
                    "direction": "outbound",
                    "carrier": f"Carrier {1 + (lane + number) % 4}",
                    "lane": lane,
                    "window_start": leave - timedelta(minutes=schedule.trailer_arrive_minutes),
                    "window_end": leave,
                    "duration_minutes": float(schedule.trailer_arrive_minutes),
                }
            )
    codes = [row["code"] for row in sku_rows]
    weights = [share[index] for index in range(len(sku_rows))]
    sim = settings.sim
    for number in range(1, sim.orders + 1):
        customer = rng.choice(customers)
        code, leave = rng.choice(departures[customer["lane"]])
        lead = rng.uniform(*sim.order_lead_hours)
        arrive = max(plan.start, leave - timedelta(hours=lead))
        count = rng.randint(*sim.lines_per_order)
        lines = [
            (sku, line_cases(rng, sim.cases_per_line)) for sku in weighted_sample(rng, codes, weights, count)
        ]
        plan.orders.append(OrderPlan(f"SO{number:05d}", customer["code"], code, leave, arrive, lines))
    plan.orders.sort(key=lambda o: (o.arrive_at, o.code))
    first = moment(day, schedule.inbound_first)
    last = moment(day, schedule.inbound_last)
    trucks = schedule.inbound_trucks
    step = (last - first) / max(trucks - 1, 1)
    pallet_need = [
        daily_cases(settings, share[index]) / row["cases_per_pallet"] for index, row in enumerate(sku_rows)
    ]
    for number in range(1, trucks + 1):
        slot = first + step * (number - 1)
        loads: dict[str, int] = {}
        lots: dict[str, tuple[str, date, date]] = {}
        for _ in range(schedule.pallets_per_truck):
            index = rng.choices(range(len(sku_rows)), weights=pallet_need)[0]
            row = sku_rows[index]
            produced = day - timedelta(days=rng.randint(1, 14))
            lots.setdefault(
                row["code"],
                (
                    f"{produced:%y%m%d}-P{1 + number % 4}",
                    produced,
                    produced + timedelta(days=row["shelf_life_days"]),
                ),
            )
            loads[row["code"]] = loads.get(row["code"], 0) + row["cases_per_pallet"]
        minutes = schedule.pallets_per_truck * schedule.unload_minutes_per_pallet
        code = f"IN-{number:02d}"
        appointment = {
            "code": code,
            "direction": "inbound",
            "carrier": f"Carrier {1 + number % 4}",
            "lane": None,
            "window_start": slot,
            "window_end": slot + timedelta(minutes=60),
            "duration_minutes": minutes,
        }
        asn = {
            "code": f"ASN{number:04d}",
            "origin": f"Plant {1 + number % 4}",
            "appointment": code,
            "lines": [
                {
                    "sku": sku,
                    "lot": lots[sku][0],
                    "produced_on": lots[sku][1].isoformat(),
                    "expires_on": lots[sku][2].isoformat(),
                    "cases": cases,
                }
                for sku, cases in loads.items()
            ],
        }
        jitter = rng.gauss(0, sim.arrival_jitter_minutes)
        plan.trucks.append(TruckPlan(appointment, asn, slot + timedelta(minutes=jitter)))
