"""Backing out of a dead end.

A kinematic bicycle yaws at ``v tan(delta) / L``, so a stopped vehicle has no
lateral authority whatsoever: it cannot turn on the spot to face a gap. Walled
in, its only way to change the angle it presents to the blockage is to back up
and come at it again - which is what a driver does, and what this adds.

Two things have to hold, and they pull against each other. It must be willing to
reverse, or it is back to standing still and waiting for a parked car to move.
And it must stop reversing, or a vehicle that backs out of a situation it still
cannot solve simply backs out again, and walks down the road one shunt at a
time. Measured against a full-width wall before the progress gate: four shunts
in fifty seconds and no progress at all.
"""
import numpy as np
import pytest

from sarathi.core.types import AgentClass
from sarathi.perception.fusion import Track
from sarathi.planning.behaviour import (Behaviour, BehaviourConfig,
                                        BehaviourPlanner, SceneSummary)
from sarathi.planning.sarathi import SarathiConfig, SarathiController
from sarathi.safety.rss import REVERSE_ROOM_MAX, SafetySupervisor
from sarathi.sim.simulator import EGO_REVERSE_LIMIT, Simulator
from sarathi.world.corridor import Corridor
from sarathi.world.scenario import scenario_from_dict

EGO_HALF_WIDTH = 0.85
ROAD_HALF_WIDTH = 3.0


def _corridor(length=200.0, half_width=4.0):
    n = int(length * 2) + 1
    return Corridor.from_spec(
        np.column_stack([np.linspace(0, length, n), np.zeros(n)]),
        [(0, half_width, half_width), (length, half_width, half_width)],
        road_type="two_way")


def _stopped(track_id, x, y, cls=AgentClass.CAR):
    return Track(id=track_id, x=x, y=y, vx=0.0, vy=0.0, P=np.eye(4) * 0.05,
                 cls=cls, confirmed=True)


def _room(tracks, s_ego=50.0, d_ego=1.8, corridor=None):
    return SafetySupervisor().reverse_room(
        (s_ego, d_ego, 0.0, 0.0), tracks, corridor or _corridor(),
        EGO_HALF_WIDTH)


# -- what is behind --------------------------------------------------------
def test_an_empty_road_behind_reports_room():
    assert _room([]) == pytest.approx(REVERSE_ROOM_MAX)


def test_a_vehicle_behind_limits_the_room_bumper_to_bumper():
    # 10 m centre to centre, less our half-length and its own.
    assert _room([_stopped(1, 40.0, 1.8)]) == pytest.approx(5.8, abs=0.01)


def test_a_vehicle_behind_but_laterally_clear_is_not_behind_us():
    assert _room([_stopped(1, 40.0, -2.5)]) == pytest.approx(REVERSE_ROOM_MAX)


def test_the_start_of_the_road_counts_as_something_behind():
    """Reversing off the end of the corridor is not an escape from anything."""
    assert _room([], s_ego=6.0) == pytest.approx(6.0)


def test_what_is_ahead_is_not_what_is_behind():
    assert _room([_stopped(1, 60.0, 1.8)]) == pytest.approx(REVERSE_ROOM_MAX)


def _approaching(track_id, x, y, speed):
    return Track(id=track_id, x=x, y=y, vx=speed, vy=0.0, P=np.eye(4) * 0.05,
                 cls=AgentClass.CAR, confirmed=True)


def test_traffic_catching_us_up_takes_the_shunt_off_the_table():
    """A gap is only room if it will still be there.

    Distance alone is not room: a car 10 m back doing 8 m/s leaves 5.8 m of
    measured gap and closes it in under a second. Backing into it makes the
    reversing vehicle the cause of the collision, and on the benchmark a
    static-distance check alone cost six collision-free runs out of sixty.
    """
    assert _room([_approaching(1, 40.0, 1.8, 8.0)]) == 0.0


def test_traffic_behind_that_is_falling_back_is_not_a_threat():
    assert _room([_approaching(1, 40.0, 1.8, -3.0)]) == pytest.approx(5.8, abs=0.01)


def test_traffic_catching_us_up_in_the_other_half_is_not_behind_us():
    assert _room([_approaching(1, 40.0, -2.5, 8.0)]) == pytest.approx(REVERSE_ROOM_MAX)


# -- when the state is chosen ----------------------------------------------
def _scene(**kw):
    base = dict(ego_speed=0.0, speed_limit=12.0, road_blocked=True,
                reverse_room=10.0, reverse_allowed=True, free_side=-1.0)
    base.update(kw)
    return SceneSummary(**base)


def _decide(scene, t=100.0):
    return BehaviourPlanner(BehaviourConfig()).decide(scene, t)


def test_walled_in_and_stopped_with_room_behind_reverses():
    d = _decide(_scene())
    assert d.state is Behaviour.REVERSE
    assert d.target_speed < 0.0, "the sign is how the controller knows"


def test_a_moving_vehicle_never_reverses():
    assert _decide(_scene(ego_speed=5.0)).state is not Behaviour.REVERSE


def test_no_room_behind_means_no_shunt():
    assert _decide(_scene(reverse_room=1.0)).state is not Behaviour.REVERSE


def test_a_road_that_is_merely_slow_is_not_a_reason_to_reverse():
    assert _decide(_scene(road_blocked=False)).state is not Behaviour.REVERSE


def test_a_crowd_is_no_place_to_reverse():
    """Backing up is for a quiet dead end, not a market.

    In dense traffic the space behind refills with filtering two-wheelers a
    moment after it is measured, and the vehicle spends the shunt across the
    carriageway with its flank exposed. Without this guard, seven benchmark
    runs that had been collision-free became collisions - every one of them
    with the ego at a standstill, six of seven struck by a two-wheeler.
    """
    assert _decide(_scene(density=40.0)).state is not Behaviour.REVERSE


def test_the_controller_can_veto_a_shunt():
    """The budget and the cooldown live on the controller, not in the guards."""
    assert _decide(_scene(reverse_allowed=False)).state is not Behaviour.REVERSE


# -- closed loop -----------------------------------------------------------
def _walled_run(duration=26.0):
    """Ego driving into a wall that spans the whole carriageway."""
    raw = {
        "name": "wall_probe", "duration": duration, "dt": 0.05,
        "chaos": 0.0, "seed": 3,
        "corridor": {
            "geometry": {"segments": [{"type": "straight", "length": 200.0}]},
            "width": [[0, ROAD_HALF_WIDTH, ROAD_HALF_WIDTH],
                      [200.0, ROAD_HALF_WIDTH, ROAD_HALF_WIDTH]],
            "road_type": "two_way", "lane_marking_quality": 0.0,
        },
        "ego": {"s": 45.0, "d": 1.5, "speed": 8.0, "goal_s": 190.0},
        # Staggered, so each is visible rather than hidden behind its neighbour.
        "agents": [{"cls": "parked_vehicle", "policy": "static", "s": s, "d": d}
                   for s, d in ((70.0, -3.0), (72.0, -0.4),
                                (74.0, 2.2), (76.0, 3.6))],
        "traffic_flow": [],
    }
    # Opt-in: reversing is off by default, so every closed-loop test here
    # turns it on explicitly - which is also the switch a demo would use.
    sim = Simulator(scenario_from_dict(raw),
                    SarathiController(SarathiConfig(use_reverse=True)),
                    record=False, live=True)
    ref = sim.corridor.reference
    rows = []
    for _ in range(int(duration / sim.dt)):
        sim.step()
        d = dict(sim.controller._debug_cache or {})
        rows.append((sim.t, sim.ego.state.speed,
                     float(ref.to_frenet(sim.ego.state.position)[0]),
                     d.get("behaviour")))
    return sim, rows


def test_a_walled_in_vehicle_actually_backs_up():
    sim, rows = _walled_run()
    assert any(r[3] == Behaviour.REVERSE.value for r in rows), \
        "never chose to reverse, with the road walled off and 30 m behind it"
    assert min(r[1] for r in rows) < -0.3, \
        "chose the state but never actually travelled backwards"


def test_reversing_stays_at_walking_pace():
    """Nothing here reverses at speed, and a vehicle that did would be worse
    than the blockage it is escaping."""
    _, rows = _walled_run()
    assert min(r[1] for r in rows) >= -EGO_REVERSE_LIMIT - 1e-6


def test_it_gives_up_rather_than_reversing_down_the_road_for_ever():
    """One shunt per blockage. The way out is not always the vehicle's to find.

    Without the progress gate this walled scene produced a shunt roughly every
    fourteen seconds, indefinitely, with the vehicle no further forward.
    """
    sim, rows = _walled_run(duration=50.0)
    shunts = sum(1 for a, b in zip(rows, rows[1:])
                 if a[3] != Behaviour.REVERSE.value
                 and b[3] == Behaviour.REVERSE.value)
    assert shunts <= 1, f"shunted {shunts} times at one blockage"
    # ...and having given up, it holds rather than drifting backwards.
    tail = [r[2] for r in rows if r[0] > 40.0]
    assert max(tail) - min(tail) < 2.0, "still wandering after giving up"


def test_the_viewer_is_told_once_the_vehicle_has_run_out_of_ideas():
    """The two halves belong together: it tries, then it asks for help."""
    sim, _ = _walled_run(duration=50.0)
    assert sim.controller._debug_cache.get("blocked_for") is not None, \
        "walled in with nothing left to try, and the viewer is not told"


def test_reversing_is_on_by_default_and_costs_nothing_measurable():
    """It was off, and the reason turned out not to be the manoeuvre.

    With reverse enabled, collision-free runs fell 43/60 to 37/60 and two of
    those collisions had the ego moving - so it shipped behind a switch. That
    cost belonged to the risk field: a parked car whose heading had been
    latched from noise laid a 9.3 m wall across the road, so the vehicle was
    forever boxed in, forever shunting, and forever sitting across the
    carriageway while it did. With that fixed the same 60 runs give 42/60
    either way and zero collisions with the ego moving.
    """
    assert SarathiConfig().use_reverse is True
    raw = {
        "name": "wall_default", "duration": 26.0, "dt": 0.05,
        "chaos": 0.0, "seed": 3,
        "corridor": {
            "geometry": {"segments": [{"type": "straight", "length": 200.0}]},
            "width": [[0, ROAD_HALF_WIDTH, ROAD_HALF_WIDTH],
                      [200.0, ROAD_HALF_WIDTH, ROAD_HALF_WIDTH]],
            "road_type": "two_way", "lane_marking_quality": 0.0,
        },
        "ego": {"s": 45.0, "d": 1.5, "speed": 8.0, "goal_s": 190.0},
        "agents": [{"cls": "parked_vehicle", "policy": "static", "s": s, "d": d}
                   for s, d in ((70.0, -3.0), (72.0, -0.4),
                                (74.0, 2.2), (76.0, 3.6))],
        "traffic_flow": [],
    }
    off = SarathiConfig(use_reverse=False)
    sim = Simulator(scenario_from_dict(raw), SarathiController(off),
                    record=False, live=True)
    speeds = []
    for _ in range(int(26.0 / sim.dt)):
        sim.step()
        speeds.append(sim.ego.state.speed)
    assert min(speeds) >= -1e-6, \
        "travelled backwards with the reverse manoeuvre switched off"


def test_an_open_road_is_untouched_by_any_of_this():
    """The shunt must be invisible unless the vehicle is genuinely walled in."""
    raw = {
        "name": "open", "duration": 30.0, "dt": 0.05, "chaos": 0.0, "seed": 3,
        "corridor": {
            "geometry": {"segments": [{"type": "straight", "length": 200.0}]},
            "width": [[0, 4.0, 4.0], [200.0, 4.0, 4.0]],
            "road_type": "two_way", "lane_marking_quality": 0.0,
        },
        "ego": {"s": 5.0, "d": 1.8, "speed": 8.0, "goal_s": 190.0},
        "agents": [{"cls": "parked_vehicle", "policy": "static",
                    "s": 60.0, "d": 1.8}],
        "traffic_flow": [],
    }
    sim = Simulator(scenario_from_dict(raw),
                    SarathiController(SarathiConfig(use_reverse=True)),
                    record=False, live=False)
    speeds = []
    for _ in range(int(30.0 / sim.dt)):
        sim.step()
        speeds.append(sim.ego.state.speed)
        if sim.finished:
            break
    assert min(speeds) >= -1e-6, "reversed on a road with a way past"
    assert sim.outcome == "goal"
