from __future__ import annotations

from collections.abc import Sequence
from itertools import pairwise
from typing import NamedTuple

from wms.config import LayoutConfig


class Point(NamedTuple):
    aisle: int
    y: float


class Layout:
    def __init__(self, cfg: LayoutConfig) -> None:
        self.cfg = cfg
        self.aisles = cfg.aisles
        self.bays = cfg.bays_per_aisle
        self.levels = cfg.levels
        self.pitch = cfg.aisle_pitch_m
        self.length = cfg.aisle_length_m
        self.depot = Point(cfg.depot_aisle, 0.0)

    def bay_y(self, bay: int) -> float:
        return self.cfg.end_gap_m + (bay + 0.5) * self.cfg.bay_width_m

    def slot(self, aisle: int, bay: int) -> Point:
        return Point(aisle, self.bay_y(bay))

    def x(self, aisle: int) -> float:
        return aisle * self.pitch

    def aisle_at(self, x: float) -> int:
        return min(max(round(x / self.pitch), 0), self.aisles - 1)

    def door_x(self, index: int, count: int) -> float:
        span = (self.aisles - 1) * self.pitch
        return span * (index + 1) / (count + 1)

    def distance(self, a: Point, b: Point) -> float:
        if a.aisle == b.aisle:
            return abs(a.y - b.y)
        across = abs(a.aisle - b.aisle) * self.pitch
        return across + min(a.y + b.y, 2 * self.length - a.y - b.y)

    def walk(self, path: Sequence[Point]) -> float:
        return sum(self.distance(a, b) for a, b in pairwise(path))

    def tour(self, stops: Sequence[Point]) -> float:
        return self.walk([self.depot, *stops, self.depot])

    def travel(self, ax: float, ay: float, bx: float, by: float) -> float:
        if ay > 0 and by > 0:
            return self.distance(Point(self.aisle_at(ax), ay), Point(self.aisle_at(bx), by))
        if ay <= 0 and by <= 0:
            return abs(ax - bx) + abs(ay - by)
        return abs(ax - bx) + abs(ay) + abs(by)
