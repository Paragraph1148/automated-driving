"""Traffic is a flow, not a fixed cast.

Reported from the live deployment: complete a run, and the second lap starts on
an empty road. The cause is that road users were only ever removed. Every one
that reached the end of the corridor was deactivated and nothing replaced it, so
a live session's population could only fall - monotonically, with no floor.

On ``highway_merge_slow`` the effect is total, because that scenario is one-way
and everything in it moves: measured over five minutes the population fell from
26 road users to 1, and by the first lap completion at t=138 s only 2 were left.
``village_road_unmarked`` looked healthier at 79% only because most of what is
in it - cattle, parked vehicles, barricades - never goes anywhere, so it was
losing the same traffic and keeping the same scenery.

A departure at one end is now an arrival at the other.
"""
import numpy as np
import pytest

from sarathi.core.types import AgentClass
from sarathi.planning.baseline import BaselineLaneFollower
from sarathi.sim.simulator import (EGO_ID, FLOW_ENTRY_INSET, REAP_THRESHOLD,
                                   Simulator)
from sarathi.world.scenario import scenario_from_dict


def _live_scene(density=0.06, live=True, length=200.0, agents=()):
    raw = {
        "name": "flow_probe", "duration": 1e9, "dt": 0.05,
        "chaos": 0.0, "seed": 5,
        "corridor": {
            "geometry": {"segments": [{"type": "straight", "length": length}]},
            "width": [[0, 6.0, 0.0], [length, 6.0, 0.0]],
            "road_type": "one_way", "lane_marking_quality": 0.8,
        },
        "ego": {"s": 5.0, "d": 1.5, "speed": 10.0, "goal_s": length - 10.0},
        "agents": list(agents),
        "traffic_flow": [{"density": density, "direction": 1,
                          "speed_scale": 0.9}],
    }
    return Simulator(scenario_from_dict(raw), BaselineLaneFollower(),
                     record=False, live=live)


def _population(sim):
    return sum(1 for aid, a in sim.agents.items()
               if aid != EGO_ID and a.active)


# -- the bug ---------------------------------------------------------------
def test_a_one_way_road_does_not_empty_out():
    """The measurement from the report, as an assertion.

    Everything here moves and the road is one-way, so without re-entry the
    population is guaranteed to reach zero; it is only a question of how long
    the viewer has to watch.
    """
    sim = _live_scene()
    start = _population(sim)
    assert start >= 6, "scene too sparse to say anything"
    trace = []
    for _ in range(3000):                       # 150 s
        sim.step()
        trace.append(_population(sim))
    # Brief dips are the entry queue waiting for a gap, which is what traffic
    # joining a road actually does. The bug was a trend, not a dip: assert on
    # where the population settles, and that it never runs out.
    settled = sum(trace[-500:]) / 500.0
    assert min(trace) > 0, "the road emptied completely"
    assert settled >= start * 0.7, (
        f"population started at {start} and settled at {settled:.1f} on a road "
        f"that is supposed to carry a steady flow")


def test_the_second_lap_starts_on_a_populated_road():
    """What the viewer actually reported: complete a run, and the world is gone."""
    sim = _live_scene()
    start = _population(sim)
    for _ in range(6000):
        sim.step()
        if any(e["kind"] == "goal reached" for e in sim.events):
            break
    else:
        pytest.skip("never completed a lap, so there is no second lap to check")
    assert _population(sim) >= start * 0.6, (
        "the ego respawned at the start of a road the traffic had already "
        "driven off the end of")


def test_a_road_user_that_drives_off_the_end_comes_back_at_the_other():
    sim = _live_scene(density=0.0, agents=[
        {"cls": "car", "policy": "traffic", "s": 190.0, "d": 1.5, "speed": 9.0}])
    assert _population(sim) == 1
    ref = sim.corridor.reference

    for _ in range(200):                      # it leaves almost immediately
        sim.step()
        if sim._pending_entry:
            break
    assert sim._pending_entry, "never drove off the end"
    assert _population(sim) == 0

    for _ in range(400):                      # and comes back when there is room
        sim.step()
        if _population(sim):
            break
    assert _population(sim) == 1, "lost it, or admitted two of it"
    back = next(a for aid, a in sim.agents.items()
                if aid != EGO_ID and a.active)
    s = float(ref.to_frenet(back.state.position)[0])
    assert s < 60.0, f"came back {s:.0f} m in, not at the start of the road"
    assert back.cls is AgentClass.CAR, "came back as something else"


# -- and it has to be well behaved about it --------------------------------
def test_nothing_re_enters_on_top_of_whatever_is_at_the_entry():
    """A vehicle materialising inside another one is a collision nobody could avoid."""
    sim = _live_scene(density=0.0)
    ref = sim.corridor.reference
    sim._pending_entry.append({
        "cls": AgentClass.CAR, "policy": "traffic", "args": {},
        "s": FLOW_ENTRY_INSET, "d": 1.5, "speed": 9.0, "aggression": 1.0})

    pos = ref.to_cartesian(FLOW_ENTRY_INSET, 1.5)       # ego sitting on the entry
    sim.ego.state.x, sim.ego.state.y = float(pos[0]), float(pos[1])
    sim._admit_entries()
    assert len(sim._pending_entry) == 1, "spawned a car inside the ego"

    pos = ref.to_cartesian(120.0, 1.5)                  # ego well clear
    sim.ego.state.x, sim.ego.state.y = float(pos[0]), float(pos[1])
    sim._admit_entries()
    assert not sim._pending_entry, "would not let it in on an empty road"
    assert _population(sim) == 1


def test_a_road_user_the_viewer_erased_stays_erased():
    """Re-entry replaces traffic that drove away, not traffic somebody removed."""
    sim = _live_scene(density=0.0, agents=[
        {"cls": "car", "policy": "traffic", "s": 90.0, "d": 1.5, "speed": 6.0}])
    car = [a for aid, a in sim.agents.items() if aid != EGO_ID][0]
    sim.despawn_near(float(car.state.x), float(car.state.y))
    for _ in range(200):
        sim.step()
    assert not sim._pending_entry
    assert _population(sim) == 0, "brought back a vehicle the viewer deleted"


def test_a_scored_run_is_not_given_extra_traffic():
    """Re-entry is a live-session behaviour and must not move the benchmark.

    A scored run is one pass down the corridor against the population the
    scenario specified. Topping it up mid-run would change every number in the
    campaign, and none of them would be comparable to the ones already reported.
    """
    sim = _live_scene(live=False)
    start = _population(sim)
    for _ in range(1200):
        sim.step()
        if sim.finished:
            break
    assert not sim._pending_entry
    assert _population(sim) <= start


def test_the_world_does_not_accumulate_the_dead():
    """Every tick walks the whole world dict, so corpses cost real time."""
    sim = _live_scene()
    for _ in range(3000):
        sim.step()
    dead = sum(1 for aid, a in sim.agents.items()
               if aid != EGO_ID and not a.active)
    assert dead <= REAP_THRESHOLD, f"{dead} retired agents still in the world"


def test_ids_are_never_handed_out_twice():
    """The viewer grabs vehicles by id; reissuing one moves somebody's hand.

    Sweeping up the dead frees their ids, and the obvious allocator - one past
    the highest in the world - would start reissuing them. A viewer whose finger
    is on vehicle 31 when 31 is swept up would find itself dragging a different
    vehicle that had just inherited the number.
    """
    sim = _live_scene()
    live = set(sim.agents)
    swept: set[int] = set()
    for _ in range(3000):
        sim.step()
        now = set(sim.agents)
        swept |= live - now
        assert not (swept & now), "an id that had been swept up was issued again"
        live = now
    assert swept, "nothing was ever swept up, so this proves nothing"
