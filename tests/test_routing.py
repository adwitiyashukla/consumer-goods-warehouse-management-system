import itertools

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from wms.config import LayoutConfig
from wms.layout import Layout, Point
from wms.routing import largest_gap, optimal, route, sshape


def make_layout(aisles: int = 4, bays: int = 5, depot: int = 0, pitch: float = 6.0) -> Layout:
    return Layout(
        LayoutConfig(
            aisles=aisles,
            bays_per_aisle=bays,
            levels=3,
            bay_width_m=2.0,
            aisle_pitch_m=pitch,
            end_gap_m=1.0,
            dock_depth_m=5.0,
            depot_aisle=depot,
            inbound_doors=1,
            outbound_doors=1,
        )
    )


def brute_force(layout: Layout, stops: list[Point]) -> float:
    unique = list(dict.fromkeys(stops))
    return min(layout.tour(list(order)) for order in itertools.permutations(unique))


@st.composite
def instances(draw: st.DrawFn) -> tuple[Layout, list[Point]]:
    aisles = draw(st.integers(1, 6))
    bays = draw(st.integers(1, 8))
    layout = make_layout(
        aisles, bays, draw(st.integers(0, aisles - 1)), draw(st.sampled_from([2.0, 6.0, 15.0]))
    )
    count = draw(st.integers(0, 6))
    stops = [
        layout.slot(draw(st.integers(0, aisles - 1)), draw(st.integers(0, bays - 1))) for _ in range(count)
    ]
    return layout, stops


@settings(max_examples=400, deadline=None)
@given(instances())
def test_optimal_matches_brute_force(case: tuple[Layout, list[Point]]) -> None:
    layout, stops = case
    best = brute_force(layout, stops) if stops else 0.0
    plan = optimal(layout, stops)
    assert plan.distance == pytest.approx(best, abs=1e-6)
    if stops:
        assert layout.tour([stops[i] for i in plan.order]) == pytest.approx(best, abs=1e-6)


@settings(max_examples=300, deadline=None)
@given(instances())
def test_heuristics_visit_everything_and_never_beat_optimal(case: tuple[Layout, list[Point]]) -> None:
    layout, stops = case
    best = optimal(layout, stops).distance
    for plan in (sshape(layout, stops), largest_gap(layout, stops)):
        assert sorted(plan.order) == list(range(len(stops)))
        assert plan.distance >= best - 1e-6
        assert plan.path[0] == layout.depot
        assert plan.path[-1] == layout.depot


def test_sshape_by_hand() -> None:
    layout = make_layout()
    stops = [layout.slot(1, 0), layout.slot(3, 4)]
    plan = sshape(layout, stops)
    assert plan.order == [0, 1]
    assert plan.distance == pytest.approx(6 + 12 + 12 + 12 + 18)


def test_largest_gap_skips_the_big_gap() -> None:
    layout = make_layout()
    stops = [layout.slot(0, 2), layout.slot(1, 0), layout.slot(1, 4), layout.slot(2, 2)]
    plan = largest_gap(layout, stops)
    assert plan.distance == pytest.approx(56.0)
    assert sshape(layout, stops).distance == pytest.approx(60.0)
    assert plan.order == [0, 2, 3, 1]


def test_same_slot_twice_is_one_stop() -> None:
    layout = make_layout()
    stops = [layout.slot(2, 3), layout.slot(2, 3)]
    plan = optimal(layout, stops)
    assert plan.order == [0, 1]
    assert plan.distance == pytest.approx(2 * 12 + 2 * layout.bay_y(3))


def test_no_stops_means_no_walking() -> None:
    layout = make_layout()
    for policy in ("sshape", "largest_gap", "optimal"):
        assert route(layout, [], policy).distance == 0.0
