from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select

from dockhand.config import ROUTINGS, Settings
from dockhand.db import make_engine
from dockhand.layout import Layout, Point
from dockhand.models import Location, Task
from dockhand.routing import route
from dockhand.sim import simulate

SCENARIOS: list[tuple[str, dict[str, dict[str, Any]]]] = [
    ("sshape, fcfs", {"picking": {"routing": "sshape", "batching": "fcfs"}}),
    ("largest_gap, fcfs", {"picking": {"routing": "largest_gap", "batching": "fcfs"}}),
    ("optimal, fcfs", {"picking": {"routing": "optimal", "batching": "fcfs"}}),
    ("optimal, savings", {"picking": {"routing": "optimal", "batching": "savings"}}),
    (
        "optimal, savings, random slots",
        {"picking": {"routing": "optimal", "batching": "savings"}, "slotting": {"policy": "random"}},
    ),
]


def row(name: str, report: dict[str, Any]) -> dict[str, Any]:
    kpis = report["kpis"]
    picking = kpis["picking"]
    hours = picking["picker_hours"]
    return {
        "scenario": name,
        "otif": kpis["shipped"]["otif"],
        "shipped": kpis["shipped"]["orders"],
        "tours": picking["tours_done"],
        "walk_km": round(picking["distance_m"] / 1000, 2),
        "picker_hours": hours,
        "stops_per_hour": round(picking["stops"] / hours, 1) if hours else None,
        "api_ms_avg": report["api_ms_avg"],
    }


async def same_tours(settings: Settings) -> dict[str, float]:
    layout = Layout(settings.layout)
    engine = make_engine(settings.database, pooled=False)
    try:
        async with engine.connect() as conn:
            rows = await conn.execute(
                select(Task.tour_id, Location.code, Location.aisle, Location.y)
                .join(Location, Task.from_location_id == Location.id)
                .where(Task.kind == "pick", Task.tour_id.is_not(None))
                .order_by(Task.tour_id, Task.seq)
            )
            tours: dict[int, dict[str, Point]] = defaultdict(dict)
            for tour, code, aisle, y in rows:
                if tour is not None and aisle is not None:
                    tours[tour].setdefault(code, Point(aisle, y))
    finally:
        await engine.dispose()
    totals = dict.fromkeys(ROUTINGS, 0.0)
    for stops in tours.values():
        for policy in ROUTINGS:
            totals[policy] += route(layout, list(stops.values()), policy).distance
    return {policy: round(meters / 1000, 2) for policy, meters in totals.items()}


async def bench(settings: Settings) -> dict[str, Any]:
    rows = []
    routes: dict[str, float] = {}
    for name, changes in SCENARIOS:
        report = await simulate(settings.with_overrides(changes))
        rows.append(row(name, report))
        if name == "optimal, savings":
            routes = await same_tours(settings)
    return {"scenarios": rows, "same_tours_km": routes}


def table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    keys = list(rows[0])
    widths = {key: max(len(key), *(len(str(r[key])) for r in rows)) for key in keys}
    lines = ["  ".join(key.ljust(widths[key]) for key in keys)]
    lines.append("  ".join("-" * widths[key] for key in keys))
    for r in rows:
        lines.append("  ".join(str(r[key]).ljust(widths[key]) for key in keys))
    return "\n".join(lines)
