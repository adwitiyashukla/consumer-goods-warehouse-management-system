from hypothesis import given, settings
from hypothesis import strategies as st

from wms.batching import Line, Unit, fcfs, savings, split
from wms.config import LayoutConfig
from wms.layout import Layout
from wms.routing import optimal

LAYOUT = Layout(
    LayoutConfig(
        aisles=8,
        bays_per_aisle=10,
        levels=3,
        bay_width_m=2.8,
        aisle_pitch_m=6.0,
        end_gap_m=2.0,
        dock_depth_m=10.0,
        depot_aisle=0,
        inbound_doors=1,
        outbound_doors=1,
    )
)


def unit(key: str, *stops: tuple[int, int], volume: float = 0.5) -> Unit:
    return Unit(key, tuple(Line(i, LAYOUT.slot(a, b), volume) for i, (a, b) in enumerate(stops)))


@st.composite
def waves(draw: st.DrawFn) -> list[Unit]:
    count = draw(st.integers(1, 9))
    units = []
    for number in range(count):
        size = draw(st.integers(1, 5))
        stops = [(draw(st.integers(0, 7)), draw(st.integers(0, 9))) for _ in range(size)]
        units.append(unit(f"o{number}", *stops, volume=draw(st.sampled_from([0.2, 0.6, 1.1]))))
    return units


def test_fcfs_keeps_order_and_capacity() -> None:
    units = [unit(f"o{i}", (i, 1), volume=1.0) for i in range(5)]
    assert fcfs(units, capacity=2.0, max_orders=4) == [[0, 1], [2, 3], [4]]
    assert fcfs(units, capacity=10.0, max_orders=3) == [[0, 1, 2], [3, 4]]


def test_savings_pairs_orders_in_the_same_aisle() -> None:
    units = [unit("a", (5, 9)), unit("b", (1, 1)), unit("c", (5, 8)), unit("d", (1, 2))]
    assert savings(LAYOUT, units, capacity=1.0, max_orders=2, routing="optimal") == [[0, 2], [1, 3]]


@settings(max_examples=150, deadline=None)
@given(waves(), st.sampled_from([1.5, 3.0]), st.integers(1, 4))
def test_savings_is_a_valid_partition_that_never_walks_more(
    units: list[Unit], capacity: float, max_orders: int
) -> None:
    groups = savings(LAYOUT, units, capacity, max_orders, "optimal")
    assert sorted(i for group in groups for i in group) == list(range(len(units)))
    for group in groups:
        assert len(group) <= max_orders
        assert len(group) == 1 or sum(units[i].volume for i in group) <= capacity + 1e-9
    walked = sum(optimal(LAYOUT, [s for i in g for s in units[i].stops]).distance for g in groups)
    alone = sum(optimal(LAYOUT, u.stops).distance for u in units)
    assert walked <= alone + 1e-6


def test_split_cuts_big_orders_into_pieces() -> None:
    lines = [Line(i, LAYOUT.slot(i % 8, i % 10), 0.9) for i in range(10)]
    pieces = split(LAYOUT, "big", lines, capacity=2.0)
    assert [piece.key for piece in pieces][:2] == ["big/1", "big/2"]
    assert all(piece.volume <= 2.0 + 1e-9 for piece in pieces)
    assert sorted(line.ref for piece in pieces for line in piece.lines) == list(range(10))


def test_split_keeps_small_orders_whole() -> None:
    lines = [Line(0, LAYOUT.slot(1, 1), 0.5)]
    assert split(LAYOUT, "small", lines, capacity=2.0)[0].key == "small"
