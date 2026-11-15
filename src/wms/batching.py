from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from wms.config import Batching, Routing
from wms.layout import Layout, Point
from wms.routing import route


@dataclass(frozen=True)
class Line:
    ref: int
    stop: Point
    volume: float


@dataclass(frozen=True)
class Unit:
    key: str
    lines: tuple[Line, ...]
    volume: float = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "volume", sum(line.volume for line in self.lines))

    @property
    def stops(self) -> list[Point]:
        return [line.stop for line in self.lines]


def sweep_key(layout: Layout, line: Line) -> tuple[int, float]:
    aisle = line.stop.aisle
    return aisle, line.stop.y if aisle % 2 == 0 else layout.length - line.stop.y


def split(layout: Layout, key: str, lines: Sequence[Line], capacity: float) -> list[Unit]:
    ordered = sorted(lines, key=lambda line: (sweep_key(layout, line), line.ref))
    pieces: list[list[Line]] = [[]]
    volume = 0.0
    for line in ordered:
        if pieces[-1] and volume + line.volume > capacity:
            pieces.append([])
            volume = 0.0
        pieces[-1].append(line)
        volume += line.volume
    if len(pieces) == 1:
        return [Unit(key, tuple(pieces[0]))]
    return [Unit(f"{key}/{number}", tuple(piece)) for number, piece in enumerate(pieces, 1)]


def fcfs(units: Sequence[Unit], capacity: float, max_orders: int) -> list[list[int]]:
    batches: list[list[int]] = []
    current: list[int] = []
    volume = 0.0
    for index, unit in enumerate(units):
        if current and (volume + unit.volume > capacity or len(current) >= max_orders):
            batches.append(current)
            current, volume = [], 0.0
        current.append(index)
        volume += unit.volume
    if current:
        batches.append(current)
    return batches


def savings(
    layout: Layout, units: Sequence[Unit], capacity: float, max_orders: int, routing: Routing
) -> list[list[int]]:
    alone = [route(layout, unit.stops, routing).distance for unit in units]
    pairs: list[tuple[float, int, int]] = []
    if max_orders > 1:
        for i in range(len(units)):
            for j in range(i + 1, len(units)):
                if units[i].volume + units[j].volume > capacity:
                    continue
                joint = route(layout, units[i].stops + units[j].stops, routing).distance
                gain = alone[i] + alone[j] - joint
                if gain > 1e-9:
                    pairs.append((-gain, i, j))
    pairs.sort()
    owner = list(range(len(units)))
    members = {index: [index] for index in range(len(units))}
    volume = {index: unit.volume for index, unit in enumerate(units)}
    for _, i, j in pairs:
        a, b = owner[i], owner[j]
        if a == b:
            continue
        if volume[a] + volume[b] > capacity or len(members[a]) + len(members[b]) > max_orders:
            continue
        for member in members[b]:
            owner[member] = a
        members[a].extend(members.pop(b))
        volume[a] += volume.pop(b)
    return sorted((sorted(group) for group in members.values()), key=lambda group: group[0])


def batch(
    layout: Layout,
    units: Sequence[Unit],
    capacity: float,
    max_orders: int,
    policy: Batching,
    routing: Routing,
) -> list[list[int]]:
    if policy == "fcfs":
        return fcfs(units, capacity, max_orders)
    return savings(layout, units, capacity, max_orders, routing)
