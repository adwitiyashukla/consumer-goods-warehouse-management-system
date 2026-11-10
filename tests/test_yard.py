from datetime import UTC, datetime, timedelta
from itertools import pairwise

import httpx
from hypothesis import given, settings
from hypothesis import strategies as st

from dockhand.generator import Plan
from dockhand.yard import Slot, assign_doors

START = datetime(2026, 3, 2, 5, 0, tzinfo=UTC)


@settings(max_examples=200, deadline=None)
@given(
    st.lists(st.tuples(st.integers(0, 600), st.integers(10, 120)), min_size=1, max_size=25),
    st.integers(1, 4),
    st.sampled_from([0.0, 10.0]),
)
def test_doors_never_overlap(requests: list[tuple[int, int]], doors: int, turn: float) -> None:
    slots = [Slot(f"T{i}", START + timedelta(minutes=a), m) for i, (a, m) in enumerate(requests)]
    plan = assign_doors(slots, [f"D{i}" for i in range(doors)], turn)
    assert set(plan) == {slot.key for slot in slots}
    by_door: dict[str, list[tuple[datetime, datetime]]] = {}
    for slot in slots:
        door, start = plan[slot.key]
        assert start >= slot.earliest
        by_door.setdefault(door, []).append((start, start + timedelta(minutes=slot.minutes + turn)))
    for spans in by_door.values():
        spans.sort()
        for (_, end), (start, _) in pairwise(spans):
            assert end <= start


async def test_truck_moves_through_the_yard(client: httpx.AsyncClient, plan: Plan) -> None:
    for truck in plan.trucks[:2]:
        appointment = {**truck.appointment}
        appointment["window_start"] = appointment["window_start"].isoformat()
        appointment["window_end"] = appointment["window_end"].isoformat()
        assert (await client.post("/api/appointments", json=appointment)).status_code == 201
    assert (await client.post("/api/yard/schedule", json={})).json() == {"scheduled": 2}
    first, second = (truck.appointment["code"] for truck in plan.trucks[:2])
    booked = {a["code"]: a for a in (await client.get("/api/appointments")).json()}
    assert booked[first]["door"] is not None
    assert (await client.post(f"/api/appointments/{first}/dock", json={})).status_code == 409
    await client.post(f"/api/appointments/{first}/arrive", json={})
    docked = await client.post(f"/api/appointments/{first}/dock", json={})
    assert docked.json()["status"] == "docked"
    await client.post(f"/api/appointments/{second}/arrive", json={})
    busy = await client.post(f"/api/appointments/{second}/dock", json={"door": docked.json()["door"]})
    assert busy.status_code == 409
    await client.post(f"/api/appointments/{first}/finish", json={})
    gone = await client.post(f"/api/appointments/{first}/depart", json={})
    assert gone.json()["gate_out_at"] is not None
    again = await client.post(f"/api/appointments/{second}/dock", json={"door": docked.json()["door"]})
    assert again.json()["status"] == "docked"
