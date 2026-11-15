from __future__ import annotations

import random
from collections.abc import Mapping, Sequence

from wms.config import Slotting
from wms.layout import Layout, Point


def ranked(frequency: Mapping[int, float]) -> list[int]:
    return sorted(frequency, key=lambda sku: (-frequency[sku], sku))


def classes(frequency: Mapping[int, float], a_share: float, b_share: float) -> dict[int, str]:
    order = ranked(frequency)
    a_count = max(1, round(len(order) * a_share))
    b_count = round(len(order) * b_share)
    labels: dict[int, str] = {}
    for rank, sku in enumerate(order):
        labels[sku] = "A" if rank < a_count else "B" if rank < a_count + b_count else "C"
    return labels


def assign(
    layout: Layout,
    slots: Sequence[tuple[int, Point]],
    frequency: Mapping[int, float],
    policy: Slotting,
    seed: int,
) -> dict[int, int]:
    if len(frequency) > len(slots):
        raise ValueError("more skus than pick slots")
    if policy == "velocity":
        chosen = sorted(slots, key=lambda slot: (layout.distance(layout.depot, slot[1]), slot[0]))
    else:
        chosen = list(slots)
        random.Random(seed).shuffle(chosen)
    return {sku: slot[0] for sku, slot in zip(ranked(frequency), chosen, strict=False)}


def expected_travel(
    layout: Layout,
    assignment: Mapping[int, int],
    points: Mapping[int, Point],
    frequency: Mapping[int, float],
) -> float:
    return sum(
        frequency[sku] * 2 * layout.distance(layout.depot, points[slot]) for sku, slot in assignment.items()
    )
