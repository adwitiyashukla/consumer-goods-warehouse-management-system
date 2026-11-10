import itertools

from dockhand.config import LayoutConfig
from dockhand.layout import Layout
from dockhand.slotting import assign, classes, expected_travel

LAYOUT = Layout(
    LayoutConfig(
        aisles=3,
        bays_per_aisle=4,
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
SLOTS = [(10 + a * 4 + b, LAYOUT.slot(a, b)) for a in range(3) for b in range(4)]
POINTS = dict(SLOTS)


def test_fastest_sku_gets_the_closest_slot() -> None:
    frequency = {1: 5.0, 2: 50.0, 3: 0.5}
    placed = assign(LAYOUT, SLOTS, frequency, "velocity", seed=1)
    nearest = min(SLOTS, key=lambda s: LAYOUT.distance(LAYOUT.depot, s[1]))[0]
    assert placed[2] == nearest


def test_velocity_slotting_is_optimal_for_expected_travel() -> None:
    frequency = {1: 9.0, 2: 1.0, 3: 4.0, 4: 2.5}
    placed = assign(LAYOUT, SLOTS, frequency, "velocity", seed=1)
    best = expected_travel(LAYOUT, placed, POINTS, frequency)
    for chosen in itertools.permutations([slot for slot, _ in SLOTS[:6]], 4):
        other = dict(zip(frequency, chosen, strict=True))
        assert best <= expected_travel(LAYOUT, other, POINTS, frequency) + 1e-9


def test_random_slotting_is_repeatable() -> None:
    frequency = {sku: float(sku) for sku in range(1, 9)}
    first = assign(LAYOUT, SLOTS, frequency, "random", seed=3)
    assert first == assign(LAYOUT, SLOTS, frequency, "random", seed=3)
    assert first != assign(LAYOUT, SLOTS, frequency, "velocity", seed=3)


def test_abc_classes_follow_the_shares() -> None:
    frequency = {sku: 100.0 - sku for sku in range(10)}
    labels = classes(frequency, a_share=0.2, b_share=0.3)
    assert [labels[sku] for sku in range(10)] == list("AABBBCCCCC")
