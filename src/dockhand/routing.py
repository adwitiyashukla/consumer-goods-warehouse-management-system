from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

from dockhand.config import Routing
from dockhand.layout import Layout, Point

State = tuple[int, int, bool, bool, bool, bool]

EMPTY: State = (0, 0, False, False, False, False)
CLOSED: State = (0, 0, False, False, False, True)
CROSSINGS = tuple((front, back) for front in range(3) for back in range(3))


@dataclass(frozen=True)
class Route:
    policy: str
    order: list[int]
    distance: float
    path: list[Point]


@dataclass(frozen=True)
class Vertical:
    name: str
    cost: float
    add_a: int
    add_b: int
    connects: bool
    segments: tuple[tuple[float, float, int], ...]


def group(stops: Sequence[Point]) -> dict[int, list[tuple[float, int]]]:
    by_aisle: dict[int, list[tuple[float, int]]] = defaultdict(list)
    for index, stop in enumerate(stops):
        by_aisle[stop.aisle].append((stop.y, index))
    for items in by_aisle.values():
        items.sort()
    return dict(by_aisle)


def empty(layout: Layout, policy: str) -> Route:
    return Route(policy, [], 0.0, [layout.depot, layout.depot])


def sshape(layout: Layout, stops: Sequence[Point]) -> Route:
    by_aisle = group(stops)
    if not by_aisle:
        return empty(layout, "sshape")
    aisles = sorted(by_aisle)
    path = [layout.depot]
    order: list[int] = []
    at_front = True
    for position, aisle in enumerate(aisles):
        items = by_aisle[aisle]
        if at_front:
            path.append(Point(aisle, 0.0))
            order.extend(index for _, index in items)
            if position == len(aisles) - 1:
                path.append(Point(aisle, items[-1][0]))
                path.append(Point(aisle, 0.0))
            else:
                path.append(Point(aisle, layout.length))
                at_front = False
        else:
            path.append(Point(aisle, layout.length))
            order.extend(index for _, index in reversed(items))
            path.append(Point(aisle, 0.0))
            at_front = True
    path.append(layout.depot)
    return Route("sshape", order, layout.walk(path), path)


def largest_gap(layout: Layout, stops: Sequence[Point]) -> Route:
    by_aisle = group(stops)
    if len(by_aisle) <= 1:
        single = sshape(layout, stops)
        return Route("largest_gap", single.order, single.distance, single.path)
    length = layout.length
    aisles = sorted(by_aisle)
    first, last, middle = aisles[0], aisles[-1], aisles[1:-1]
    split: dict[int, int] = {}
    for aisle in middle:
        edges = [0.0, *(y for y, _ in by_aisle[aisle]), length]
        gaps = [edges[k + 1] - edges[k] for k in range(len(edges) - 1)]
        split[aisle] = max(range(len(gaps)), key=gaps.__getitem__)
    path = [layout.depot, Point(first, 0.0), Point(first, length)]
    order = [index for _, index in by_aisle[first]]
    for aisle in middle:
        upper = by_aisle[aisle][split[aisle] :]
        if upper:
            path.extend([Point(aisle, length), Point(aisle, upper[0][0]), Point(aisle, length)])
            order.extend(index for _, index in reversed(upper))
    path.extend([Point(last, length), Point(last, 0.0)])
    order.extend(index for _, index in reversed(by_aisle[last]))
    for aisle in reversed(middle):
        lower = by_aisle[aisle][: split[aisle]]
        if lower:
            path.extend([Point(aisle, 0.0), Point(aisle, lower[-1][0]), Point(aisle, 0.0)])
            order.extend(index for _, index in lower)
    path.append(layout.depot)
    return Route("largest_gap", order, layout.walk(path), path)


def verticals(ys: list[float], length: float) -> list[Vertical]:
    if not ys:
        return [
            Vertical("none", 0.0, 0, 0, False, ()),
            Vertical("pass", length, 1, 1, True, ((0.0, length, 1),)),
            Vertical("double", 2 * length, 2, 2, True, ((0.0, length, 2),)),
        ]
    options = [
        Vertical("pass", length, 1, 1, True, ((0.0, length, 1),)),
        Vertical("double", 2 * length, 2, 2, True, ((0.0, length, 2),)),
        Vertical("top", 2 * (length - ys[0]), 0, 2, False, ((ys[0], length, 2),)),
        Vertical("bottom", 2 * ys[-1], 2, 0, False, ((0.0, ys[-1], 2),)),
    ]
    if len(ys) > 1:
        k = max(range(len(ys) - 1), key=lambda i: ys[i + 1] - ys[i])
        cost = 2 * (length - (ys[k + 1] - ys[k]))
        segments = ((0.0, ys[k], 2), (ys[k + 1], length, 2))
        options.append(Vertical("gap", cost, 2, 2, False, segments))
    return options


def apply_vertical(state: State, option: Vertical) -> State | None:
    pa, pb, ta, tb, conn, closed = state
    if closed:
        return state if not option.segments else None
    nta = ta or option.add_a > 0
    ntb = tb or option.add_b > 0
    nconn = nta and ntb and (option.connects or (conn and ta and tb))
    return ((pa + option.add_a) % 2, (pb + option.add_b) % 2, nta, ntb, nconn, False)


def apply_crossing(state: State, front: int, back: int, depot_here: bool) -> State | None:
    pa, pb, ta, tb, conn, closed = state
    if closed:
        return state if front == 0 and back == 0 and not depot_here else None
    if (pa + front) % 2 or (pb + back) % 2:
        return None
    a_used = ta or front > 0
    b_used = tb or back > 0
    if depot_here and not a_used:
        return None
    if not a_used and not b_used:
        return EMPTY
    if conn and ta and tb:
        if front == 0 and back == 0:
            return CLOSED
        return (front % 2, back % 2, front > 0, back > 0, front > 0 and back > 0, False)
    a_left = a_used and front == 0
    b_left = b_used and back == 0
    if a_left or b_left:
        if (a_left and b_used) or (b_left and a_used):
            return None
        return CLOSED
    return (front % 2, back % 2, front > 0, back > 0, False, False)


def euler(edges: list[tuple[Point, Point]], start: Point) -> list[Point]:
    adjacency: dict[Point, list[tuple[Point, int]]] = defaultdict(list)
    for index, (u, v) in enumerate(edges):
        adjacency[u].append((v, index))
        adjacency[v].append((u, index))
    used = [False] * len(edges)
    cursor: dict[Point, int] = defaultdict(int)
    stack = [start]
    circuit: list[Point] = []
    while stack:
        node = stack[-1]
        neighbors = adjacency[node]
        while cursor[node] < len(neighbors) and used[neighbors[cursor[node]][1]]:
            cursor[node] += 1
        if cursor[node] == len(neighbors):
            circuit.append(stack.pop())
        else:
            nxt, index = neighbors[cursor[node]]
            used[index] = True
            stack.append(nxt)
    circuit.reverse()
    return circuit


def optimal(layout: Layout, stops: Sequence[Point]) -> Route:
    by_aisle = group(stops)
    if not by_aisle:
        return empty(layout, "optimal")
    depot = layout.depot
    length = layout.length
    lo = min(min(by_aisle), depot.aisle)
    hi = max(max(by_aisle), depot.aisle)
    levels = {aisle: sorted({y for y, _ in items}) for aisle, items in by_aisle.items()}
    layer: dict[State, float] = {EMPTY: 0.0}
    pointers: list[dict[State, tuple[State, Vertical | tuple[int, int]]]] = []
    for aisle in range(lo, hi + 1):
        options = verticals(levels.get(aisle, []), length)
        step: dict[State, float] = {}
        pointer: dict[State, tuple[State, Vertical | tuple[int, int]]] = {}
        for state, cost in layer.items():
            for option in options:
                nxt = apply_vertical(state, option)
                if nxt is not None and cost + option.cost < step.get(nxt, math.inf) - 1e-9:
                    step[nxt] = cost + option.cost
                    pointer[nxt] = (state, option)
        pointers.append(pointer)
        layer, step, pointer = step, {}, {}
        crossings = ((0, 0),) if aisle == hi else CROSSINGS
        for state, cost in layer.items():
            for front, back in crossings:
                nxt = apply_crossing(state, front, back, aisle == depot.aisle)
                extra = (front + back) * layout.pitch
                if nxt is not None and cost + extra < step.get(nxt, math.inf) - 1e-9:
                    step[nxt] = cost + extra
                    pointer[nxt] = (state, (front, back))
        pointers.append(pointer)
        layer = step
    if CLOSED not in layer:
        raise RuntimeError("no tour found")
    choices: list[Vertical | tuple[int, int]] = []
    state = CLOSED
    for pointer in reversed(pointers):
        state, choice = pointer[state]
        choices.append(choice)
    choices.reverse()
    edges: list[tuple[Point, Point]] = []
    for offset, aisle in enumerate(range(lo, hi + 1)):
        chosen = choices[2 * offset]
        crossing = choices[2 * offset + 1]
        assert isinstance(chosen, Vertical) and isinstance(crossing, tuple)
        marks = [0.0, *levels.get(aisle, []), length]
        for start, end, copies in chosen.segments:
            inside = [mark for mark in marks if start <= mark <= end]
            for low, high in pairwise(inside):
                edges.extend([(Point(aisle, low), Point(aisle, high))] * copies)
        edges.extend([(Point(aisle, 0.0), Point(aisle + 1, 0.0))] * crossing[0])
        edges.extend([(Point(aisle, length), Point(aisle + 1, length))] * crossing[1])
    circuit = euler(edges, depot)
    at_point: dict[Point, list[int]] = defaultdict(list)
    for index, stop in enumerate(stops):
        at_point[Point(stop.aisle, stop.y)].append(index)
    order: list[int] = []
    for point in circuit:
        if point in at_point:
            order.extend(at_point.pop(point))
    return Route("optimal", order, layout.walk(circuit), circuit)


def route(layout: Layout, stops: Sequence[Point], policy: Routing) -> Route:
    if policy == "sshape":
        return sshape(layout, stops)
    if policy == "largest_gap":
        return largest_gap(layout, stops)
    return optimal(layout, stops)
