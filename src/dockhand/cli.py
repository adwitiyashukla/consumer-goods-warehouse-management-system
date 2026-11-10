from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, time
from functools import partial
from pathlib import Path
from typing import Any

import uvicorn

from dockhand.app import create_app
from dockhand.bench import bench, table
from dockhand.config import Settings, load_settings
from dockhand.db import migrate
from dockhand.sim import demo, simulate, summary


def until(settings: Settings, clock: str | None) -> datetime | None:
    if clock is None:
        return None
    return datetime.combine(settings.schedule.date, time.fromisoformat(clock), tzinfo=UTC)


def overrides(args: argparse.Namespace) -> dict[str, dict[str, Any]]:
    changes: dict[str, dict[str, Any]] = {}
    if args.routing:
        changes.setdefault("picking", {})["routing"] = args.routing
    if args.batching:
        changes.setdefault("picking", {})["batching"] = args.batching
    if args.slotting:
        changes.setdefault("slotting", {})["policy"] = args.slotting
    if args.seed is not None:
        changes.setdefault("sim", {})["seed"] = args.seed
    return changes


def failed(report: dict[str, Any]) -> bool:
    kpis = report["kpis"]
    return (
        bool(kpis["stock"]["oversold_pallets"])
        or not kpis["orders"]["total"]
        or not kpis["picking"]["tours_done"]
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="dockhand")
    parser.add_argument("--config", default=None)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate")
    serve = commands.add_parser("serve")
    serve.add_argument("--demo", action="store_true")
    serve.add_argument("--fresh", action="store_true")
    sim = commands.add_parser("sim")
    sim.add_argument("--until", default=None)
    sim.add_argument("--url", default=None)
    sim.add_argument("--routing", choices=["sshape", "largest_gap", "optimal"])
    sim.add_argument("--batching", choices=["fcfs", "savings"])
    sim.add_argument("--slotting", choices=["velocity", "random"])
    sim.add_argument("--seed", type=int, default=None)
    sim.add_argument("--check", action="store_true")
    sim.add_argument("--json", action="store_true")
    compare = commands.add_parser("bench")
    compare.add_argument("--out", default="results/bench.json")
    args = parser.parse_args(argv)
    settings = load_settings(args.config)
    migrate(settings.database.url)
    if args.command == "migrate":
        return
    if args.command == "serve":
        warmup = partial(demo, settings, args.fresh) if args.demo else None
        uvicorn.run(create_app(settings, warmup), host=settings.server.host, port=settings.server.port)
        return
    if args.command == "sim":
        settings = settings.with_overrides(overrides(args))
        report = asyncio.run(simulate(settings, until=until(settings, args.until), url=args.url))
        print(json.dumps(report, indent=2) if args.json else summary(report))
        if args.check and failed(report):
            sys.exit(1)
        return
    result = asyncio.run(bench(settings))
    print(table(result["scenarios"]))
    print()
    print("same tours, three routers (km):", result["same_tours_km"])
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
