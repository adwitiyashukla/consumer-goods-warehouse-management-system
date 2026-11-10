from typing import Any

from dockhand.config import Settings
from dockhand.sim import simulate


def stable(report: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in report.items() if key != "api_ms_avg"}


async def test_small_day_runs_clean(settings: Settings) -> None:
    report = await simulate(settings)
    kpis = report["kpis"]
    assert kpis["orders"]["total"] == settings.sim.orders
    assert kpis["shipped"]["orders"] == settings.sim.orders
    assert kpis["stock"]["oversold_pallets"] == 0
    assert kpis["picking"]["tours_done"] > 0
    assert kpis["inbound"]["trucks"] == settings.schedule.inbound_trucks
    assert stable(report) == stable(await simulate(settings))


async def test_policies_change_the_walk(settings: Settings) -> None:
    walk = {}
    for routing in ("sshape", "optimal"):
        report = await simulate(
            settings.with_overrides({"picking": {"routing": routing, "batching": "fcfs"}})
        )
        walk[routing] = report["kpis"]["picking"]["distance_m"]
    assert walk["optimal"] < walk["sshape"]
