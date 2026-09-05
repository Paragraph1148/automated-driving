"""The safety supervisor must slow the vehicle, not immobilise it.

The standoff rule exists so the ego does not creep to within centimetres of a
stopped leader and lose the room a lateral manoeuvre needs. It used to enforce
that by clamping the speed cap to a hard zero, which produced exactly the
failure it was written to prevent.

A kinematic bicycle yaws at ``v tan(delta) / L``. At zero speed the steering
has no authority at all, so a vehicle forbidden to move cannot turn out from
behind whatever it is stopped behind - it can only wait for that thing to move,
and a parked car, a barricade or a cow that has sat down never will. Measured
on ``village_road_unmarked`` before the fix: the ego stood still for 23-79% of
every run, in stretches of up to 32 seconds, and never once reached its goal.
Every single zero speed cap in those runs came from this one rule.

These tests pin both halves of the invariant: the vehicle keeps a crawl when
there is a way past, and it is still brought to a genuine halt when there is
not.
"""
import itertools

import numpy as np
import pytest

from sarathi.core.types import AgentClass, params_for
from sarathi.perception.fusion import Track
from sarathi.planning.sarathi import SarathiController
from sarathi.safety.rss import (RSSParams, SafetySupervisor,
                                max_safe_speed_same_direction)
from sarathi.sim.simulator import Simulator
from sarathi.world.corridor import Corridor
from sarathi.world.scenario import scenario_from_dict

EGO_HALF_WIDTH = 0.85
#: The half-length evaluate() subtracts for the ego's own body.
EGO_HALF_LENGTH = 2.1
ROAD_HALF_WIDTH = 4.0


def _corridor(half_width=ROAD_HALF_WIDTH, length=200.0):
    n = int(length * 2) + 1
    return Corridor.from_spec(
        np.column_stack([np.linspace(0, length, n), np.zeros(n)]),
        [(0, half_width, half_width), (length, half_width, half_width)],
        road_type="two_way")


def _stopped(track_id, x, y, cls=AgentClass.CAR):
    """A stationary road user at a world position, as the tracker would hand it over."""
    return Track(id=track_id, x=x, y=y, vx=0.0, vy=0.0, P=np.eye(4) * 0.05,
                 cls=cls, confirmed=True)


def _verdict(tracks, ego_d=1.8, ego_speed=0.0, corridor=None, requested=1.5):
    corridor = corridor or _corridor()
    sup = SafetySupervisor()
    return sup.evaluate(ego_speed, (50.0, ego_d, ego_speed, 0.0), tracks,
                        corridor, EGO_HALF_WIDTH, 0.05, requested)


# -- the invariant ---------------------------------------------------------
def test_standoff_keeps_a_crawl_when_there_is_a_way_past():
    """Stopped car dead ahead, 1.2 m away, and an empty half of road beside it."""
    v = _verdict([_stopped(1, 55.0, 1.8)])
    assert v.speed_cap > 0.0, (
        "the vehicle is held at a dead stop behind a stopped car with the "
        "whole other half of the road clear - it can never steer out")
    assert "standoff" in v.binding_reason


def test_standoff_still_stops_dead_when_the_road_is_blocked():
    """The other half of the invariant: nowhere to go means stop, as before."""
    blocked = [_stopped(i, 55.0, d) for i, d in
               enumerate([-3.2, -1.6, 0.0, 1.8, 3.4], start=1)]
    v = _verdict(blocked)
    assert v.speed_cap == 0.0, "a fully blocked carriageway must still stop it"
    assert v.binding_reason.startswith("boxed-in")


def test_a_road_narrower_than_the_vehicle_offers_no_escape():
    narrow = _corridor(half_width=1.0)
    v = _verdict([_stopped(1, 55.0, 0.0)], ego_d=0.0, corridor=narrow)
    assert v.speed_cap == 0.0


def test_the_crawl_never_overrides_the_rss_gap_term():
    """The crawl may govern inside the standoff band, never at contact range.

    This is what makes the change safe rather than merely convenient: the RSS
    term is still evaluated on the true gap and still reaches zero on its own,
    so no amount of lateral escape lets the vehicle drive into anything.
    """
    p = RSSParams()
    corridor = _corridor()
    # Walk a stopped car in from beyond the standoff to bumper contact.
    for gap_wanted in (3.0, 2.0, 1.5, 1.0, 0.5, 0.2, 0.0):
        # evaluate() measures gap = ds - ego_half_length - track_half_length.
        x = (50.0 + gap_wanted + EGO_HALF_LENGTH
             + params_for(AgentClass.CAR).length / 2.0)
        v = _verdict([_stopped(1, x, 1.8)], corridor=corridor)
        rss = max_safe_speed_same_direction(gap_wanted, 0.0, p, 7.5)
        assert v.speed_cap <= rss + 1e-6, (
            f"at a {gap_wanted:.1f} m gap the cap {v.speed_cap:.3f} exceeds "
            f"what RSS permits ({rss:.3f})")
    # ...and at contact range it is zero however much room is beside it.
    touching = _verdict([_stopped(1, 50.0 + EGO_HALF_LENGTH +
                                  params_for(AgentClass.CAR).length / 2.0, 1.8)],
                        corridor=corridor)
    assert touching.speed_cap == pytest.approx(0.0, abs=1e-6)


def test_the_supervisor_can_still_only_ever_slow_us_down():
    """The monotonicity the whole layer rests on, unaffected by the change."""
    v = _verdict([_stopped(1, 55.0, 1.8)], requested=2.0)
    assert v.accel_cap <= 2.0 + 1e-9
    clear = _verdict([], requested=2.0)
    assert clear.accel_cap == pytest.approx(2.0)
    assert not clear.intervened


# -- the blocked report ----------------------------------------------------
def _blocked(tracks, ego_d=1.8, ego_speed=0.0, corridor=None):
    corridor = corridor or _corridor()
    return SafetySupervisor().road_blocked(
        ego_speed, (50.0, ego_d, ego_speed, 0.0), tracks, corridor,
        EGO_HALF_WIDTH)


def test_a_moving_vehicle_is_never_reported_as_walled_in():
    """Slowing for a leader is not the same as having nowhere to go."""
    wall = [_stopped(i, x, d) for i, (x, d) in
            enumerate([(58.0, -3.0), (60.0, -0.4), (62.0, 2.2)], start=1)]
    assert _blocked(wall, ego_speed=4.0) == (False, None)


def test_a_clear_band_beside_the_obstruction_is_not_blocked():
    assert _blocked([_stopped(1, 55.0, 1.8)]) == (False, None)


def test_a_wall_across_the_carriageway_is_reported_with_its_nearest_member():
    """What the viewer is told: blocked, and which one to reach for.

    Staggered rather than abreast, because a rank of vehicles at one station
    occludes itself and the tracker only ever sees the outermost - the report
    is built from what the vehicle perceives, not from ground truth.
    """
    wall = [_stopped(1, 62.0, -3.0), _stopped(2, 54.4, -0.4),
            _stopped(3, 58.0, 2.2), _stopped(4, 64.0, 3.6)]
    blocked, who = _blocked(wall)
    assert blocked, "a walled carriageway must be reported to the viewer"
    # The track itself, not its id: a track id belongs to the tracker's own
    # numbering, and matching it against the viewer's list of simulator agents
    # puts the marker on whichever unrelated vehicle shares the number.
    assert who is not None and who.x == pytest.approx(54.4), (
        f"should name the nearest blocker, named the one at x={who and who.x}")
    assert who.cls is AgentClass.CAR


def test_the_blocked_report_never_drives_anything():
    """It is advice for a person, not an input to control.

    The verdict the vehicle acts on must be identical whether or not the road
    is walled - otherwise a viewer-facing hint has become a control path.
    """
    wall = [_stopped(1, 62.0, -3.0), _stopped(2, 54.4, -0.4),
            _stopped(3, 58.0, 2.2), _stopped(4, 64.0, 3.6)]
    sup = SafetySupervisor()
    args = (0.0, (50.0, 1.8, 0.0, 0.0), wall, _corridor(), EGO_HALF_WIDTH)
    before = sup.evaluate(*args, 0.05, 1.5)
    sup.road_blocked(*args)
    after = sup.evaluate(*args, 0.05, 1.5)
    assert (before.speed_cap, before.accel_cap) == (after.speed_cap,
                                                    after.accel_cap)


# -- closed loop -----------------------------------------------------------
def test_the_ego_gets_past_a_parked_car_instead_of_waiting_for_it():
    """End to end: the failure a viewer actually sees, on the smallest scene.

    A parked car in the ego's own track, an empty road either side, and nothing
    else at all. Before the fix the ego stopped 2 m short and stayed there for
    the rest of the run.
    """
    raw = {
        "name": "standoff_probe", "duration": 30.0, "dt": 0.05,
        "chaos": 0.0, "seed": 4,
        "corridor": {
            "geometry": {"segments": [{"type": "straight", "length": 200.0}]},
            "width": [[0, ROAD_HALF_WIDTH, ROAD_HALF_WIDTH],
                      [200.0, ROAD_HALF_WIDTH, ROAD_HALF_WIDTH]],
            "road_type": "two_way", "lane_marking_quality": 0.0,
        },
        "ego": {"s": 5.0, "d": 1.8, "speed": 8.0, "goal_s": 190.0},
        "agents": [{"cls": "parked_vehicle", "policy": "static",
                    "s": 60.0, "d": 1.8}],
        "traffic_flow": [],
    }
    # live=False so the run *ends* at the goal. In live mode reaching the goal
    # puts the ego back at the start for the next visitor, so a final arc length
    # measures how far into the next lap it got, not how far it travelled.
    sim = Simulator(scenario_from_dict(raw), SarathiController(),
                    record=False, live=False)
    ref = sim.corridor.reference
    speeds = []
    for _ in range(int(30.0 / sim.dt)):
        sim.step()
        speeds.append(sim.ego.state.speed)
        if sim.finished:
            break

    longest = max((len(list(g)) for k, g in
                   itertools.groupby(speeds, key=lambda v: v < 0.3) if k),
                  default=0) * sim.dt
    reached = float(ref.to_frenet(sim.ego.state.position)[0])
    assert sim.outcome == "goal", (
        f"the run ended {sim.outcome!r} at s={reached:.0f} m, with one parked "
        f"car at 60 m on an otherwise empty road")
    assert longest < 3.0, (
        f"the ego stood still for {longest:.1f} s in a row behind a parked car")
