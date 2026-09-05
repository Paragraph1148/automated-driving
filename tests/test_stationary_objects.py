"""What the stack believes about things that are not moving.

Four bugs reported from a live deployment, all of them about stationary
objects, and all of them ending with the vehicle stopped in the road:

* a parked car's risk kernel laid square across a carriageway it was parallel
  to, because its heading had been latched from a metre of position noise and
  nothing ever revised it;
* a red risk field over empty road, because perception kept coasting a track
  for a vehicle that had been erased from the world;
* every moving trajectory rejected as a collision while the safety supervisor
  reported the road clear, because that rotated kernel really did block it;
* and the vehicle stopped level with the car it was overtaking and staying
  there for the rest of the run.

A stationary object is the hard case for a tracker, not the easy one. Its
filtered velocity is noise with metres per second of standard deviation and its
filtered position random-walks; every quantity derived from either is therefore
suspect, and each of these bugs is one such quantity being trusted.
"""
import math

import numpy as np
import pytest

from sarathi.core.types import AgentClass
from sarathi.perception.fusion import (HEADING_TRAVEL, HEADING_WINDOW,
                                       MOVING_SPEED, Track, Tracker)
from sarathi.planning.sarathi import SarathiController
from sarathi.sim.simulator import Simulator
from sarathi.world.scenario import scenario_from_dict


def _track(**kw):
    base = dict(id=1, x=0.0, y=0.0, vx=0.0, vy=0.0, P=np.eye(4) * 0.05,
                cls=AgentClass.CAR, confirmed=True, hits=10, age=1.0)
    base.update(kw)
    return Track(**base)


# -- a heading has to be earned --------------------------------------------
def test_a_track_that_has_not_travelled_has_no_heading():
    """Position noise must not be mistaken for a direction of travel.

    The tracker used to fix a heading from the instantaneous filtered velocity,
    which for a stationary object is noise: it latched whichever way the noise
    pointed and never revised it. Measured on a parked car beside an empty
    road, the result was a heading of +100.7 degrees to a road it was parallel
    to.
    """
    tk = Tracker(dt=0.1)
    tr = _track()
    tk.tracks[1] = tr
    # Jitter about a fixed point for a long time, never going anywhere.
    rng = np.random.default_rng(0)
    for _ in range(200):
        tr.x, tr.y = rng.normal(0.0, 0.25, 2)
        tr.age += 0.1
        tk._update_heading(tr)
    assert not tr.has_heading, "earned a heading by standing still and jittering"


def test_a_track_that_travels_earns_a_heading():
    tk = Tracker(dt=0.1)
    tr = _track()
    tk.tracks[1] = tr
    for _ in range(30):                       # 3 m north-east in 3 s
        tr.x += 0.1 * math.cos(math.radians(45))
        tr.y += 0.1 * math.sin(math.radians(45))
        tr.age += 0.1
        tk._update_heading(tr)
    assert tr.has_heading
    assert tr.heading == pytest.approx(math.radians(45), abs=math.radians(5))


def test_a_heading_expires_once_the_object_stops_going_anywhere():
    """The bug that survived the first fix: nothing ever cleared a bad heading.

    Requiring travel stopped new headings being latched from noise, but a
    heading taken before the vehicle settled outlived the whole run. A full
    window without covering the ground is positive evidence it is not
    travelling, and the heading has to go with it.
    """
    tk = Tracker(dt=0.1)
    tr = _track(heading_estimate=math.radians(100.0), has_heading=True)
    tk.tracks[1] = tr
    for _ in range(int((HEADING_WINDOW + 1.0) / 0.1)):
        tr.age += 0.1
        tk._update_heading(tr)               # never moves
    assert not tr.has_heading, \
        "a stationary object kept a heading fixed from noise"


def test_travelling_slowly_still_earns_a_heading_eventually():
    """A cow at half a metre a second is travelling, not drifting."""
    tk = Tracker(dt=0.1)
    tr = _track()
    tk.tracks[1] = tr
    for _ in range(int(HEADING_WINDOW / 0.1)):
        tr.x += 0.06                          # 0.6 m/s
        tr.age += 0.1
        tk._update_heading(tr)
    assert tr.has_heading
    assert abs(tr.heading) < math.radians(5)


def test_moving_is_about_speed_not_about_orientation():
    """A vehicle first seen closing at 14 m/s is moving on its first frame.

    Coupling the two would have every newly detected vehicle predicted
    stationary for the metre it takes to fix an orientation - the one metre
    that matters most.
    """
    fast = _track(vx=14.0)
    assert fast.is_moving and not fast.has_heading
    assert not _track(vx=MOVING_SPEED * 0.5).is_moving


# -- the world can remove things -------------------------------------------
def test_a_track_can_be_told_its_object_no_longer_exists():
    tk = Tracker(dt=0.1)
    tk.tracks[1] = _track(x=10.0, y=0.0)
    tk.tracks[2] = _track(id=2, x=40.0, y=0.0)
    assert tk.forget([(10.5, 0.2)]) == 1
    assert set(tk.tracks) == {2}, "forgot the wrong one, or too many"


def test_forgetting_nothing_in_particular_removes_nothing():
    tk = Tracker(dt=0.1)
    tk.tracks[1] = _track(x=10.0, y=0.0)
    assert tk.forget([(80.0, 0.0)]) == 0
    assert tk.forget([]) == 0
    assert set(tk.tracks) == {1}


def test_erasing_a_vehicle_takes_its_risk_with_it():
    """End to end: the red field over empty road.

    Coasting exists so an object hidden behind a bus is not forgotten, and no
    sensor can tell "occluded" from "gone". But in this sandbox things really
    are removed - a viewer erases one, or the simulator clears one the vehicle
    has touched - and the world can say so directly. Before it did, the risk
    field kept a blob over the empty road for more than a second afterwards.
    """
    sim = _scene([{"cls": "parked_vehicle", "policy": "static",
                   "s": 40.0, "d": 1.8}])
    for _ in range(60):
        sim.step()
    assert any(t.truth_id == 1 for t in sim.controller.tracks), \
        "never saw the car in the first place"
    sim.despawn_near(*_world_of(sim, 40.0, 1.8))
    sim.step()
    assert not any(t.truth_id == 1 for t in sim.controller.tracks), \
        "still tracking a vehicle that has been removed from the world"


# -- and the whole thing, end to end ---------------------------------------
def _world_of(sim, s, d):
    p = sim.corridor.reference.to_cartesian(s, d)
    return float(p[0]), float(p[1])


def _scene(agents, duration=60.0, live=True):
    raw = {
        "name": "stationary_probe", "duration": duration, "dt": 0.05,
        "chaos": 0.0, "seed": 3,
        "corridor": {
            "geometry": {"segments": [{"type": "straight", "length": 200.0}]},
            "width": [[0, 4.0, 4.0], [200.0, 4.0, 4.0]],
            "road_type": "two_way", "lane_marking_quality": 0.0,
        },
        "ego": {"s": 5.0, "d": 1.8, "speed": 8.0, "goal_s": 190.0},
        "agents": agents, "traffic_flow": [],
    }
    return Simulator(scenario_from_dict(raw), SarathiController(),
                     record=False, live=live)


def test_it_gets_past_a_single_parked_car_on_an_empty_road():
    """The simplest scene there is, and it could not do it.

    One parked car, an empty road either side, nothing else at all. The car's
    heading was latched at +100.7 degrees from position noise, which laid its
    4.65 m core half-length across the carriageway and turned a 4.2 m car into
    a 9.3 m wall. The ego drew level with it - 1.58 m clear of it - found every
    moving trajectory rejected as a collision while the safety supervisor
    reported the road clear, and stood there for the rest of the run.
    """
    sim = _scene([{"cls": "parked_vehicle", "policy": "static",
                   "s": 60.0, "d": 1.8}], live=False)
    ref = sim.corridor.reference
    for _ in range(int(60.0 / sim.dt)):
        sim.step()
        if sim.finished:
            break
    reached = float(ref.to_frenet(sim.ego.state.position)[0])
    assert sim.outcome == "goal", (
        f"ended {sim.outcome!r} at s={reached:.0f} m with one parked car at 60 m")


def test_a_parked_car_is_not_believed_to_lie_across_the_road():
    """The measurement behind the screenshot, as an assertion."""
    sim = _scene([{"cls": "parked_vehicle", "policy": "static",
                   "s": 40.0, "d": 1.8}])
    worst = 0.0
    for _ in range(200):
        sim.step()
        for pred in (sim.controller.predictions or []):
            if pred.cls is not AgentClass.PARKED_VEHICLE or not pred.modes:
                continue
            th = float(max(pred.modes, key=lambda m: m.probability).headings[0])
            # The road here runs along +x, and the car is parallel to it. A
            # body is symmetric end to end, so fold 180 degrees out onto zero.
            err = abs(float(np.arctan2(np.sin(th), np.cos(th))))
            worst = max(worst, min(err, math.pi - err))
    assert worst < math.radians(30.0), (
        f"a parked car parallel to the road was believed to sit "
        f"{math.degrees(worst):.0f} degrees across it")
