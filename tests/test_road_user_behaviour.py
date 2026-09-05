"""Closed-loop guardrails for how the *other* road users behave.

Every assertion here is a bug a viewer reported by describing what they saw on
the screen, expressed as a number so it cannot come back quietly:

* wrong-way riders that crabbed sideways down the road instead of facing the way
  they were travelling,
* oncoming traffic that would have driven down the ego's own half of the road,
* cattle that never once stood still, and ran when they moved.

They run a deliberately small scene rather than a scenario file. The cost of a
tick is the traffic - sixty agents each evaluating NLB-IDM against their
neighbours - so a full scenario is ~25 ms a step, and a behaviour that needs
twenty seconds of settling to show up would put a minute on the suite for each
assertion. A handful of agents on a straight road isolates the behaviour and
runs in well under a second.
"""
import math

import numpy as np
import pytest

from sarathi.core.types import AgentClass
from sarathi.planning.baseline import BaselineLaneFollower
from sarathi.sim.simulator import HEADING_CONE, Simulator
from sarathi.world.corridor import Corridor
from sarathi.world.scenario import scenario_from_dict

ROAD_LENGTH = 240.0
HALF_WIDTH = 4.0


def _scene(agents, duration=20.0, seed=5):
    """A straight, empty two-way road carrying only the agents under test."""
    raw = {
        "name": "behaviour_probe",
        "duration": duration, "dt": 0.05, "chaos": 0.0, "seed": seed,
        "corridor": {
            "geometry": {"segments": [{"type": "straight",
                                       "length": ROAD_LENGTH}]},
            "width": [[0, HALF_WIDTH, HALF_WIDTH],
                      [ROAD_LENGTH, HALF_WIDTH, HALF_WIDTH]],
            "road_type": "two_way", "lane_marking_quality": 0.0,
        },
        "ego": {"s": 4.0, "d": 2.0, "speed": 8.0, "goal_s": ROAD_LENGTH - 10.0},
        "agents": agents,
        "traffic_flow": [],
    }
    scenario = scenario_from_dict(raw)
    # live=True so contact is recorded and the world keeps running: these tests
    # are about steady-state behaviour, and a run that halts on first contact
    # measures whatever happened in the first two seconds instead.
    return Simulator(scenario, BaselineLaneFollower(), record=False, live=True)


def _run(sim, watch):
    """Step the scene, sampling ``watch(agent)`` for each live watched agent."""
    out = {aid: [] for aid in watch}
    steps = int(round(sim.scenario.duration / sim.dt))
    for _ in range(steps):
        sim.step()
        for aid, probe in watch.items():
            agent = sim.agents.get(aid)
            if agent is not None and agent.active:
                out[aid].append(probe(sim, agent))
    return out


def _heading_error_from_reverse(sim, agent):
    """Radians between an agent's heading and the *reversed* road direction."""
    s, _ = sim.corridor.reference.to_frenet(agent.state.position)
    road = float(sim.corridor.reference.heading_at(s)) + math.pi
    return abs(float(np.arctan2(np.sin(agent.state.heading - road),
                                np.cos(agent.state.heading - road))))


def _offset(sim, agent):
    return float(sim.corridor.reference.to_frenet(agent.state.position)[1])


# -- reverse-direction traffic -------------------------------------------
def _reverse_scene():
    return _scene([
        {"cls": "two_wheeler", "policy": "wrong_way",
         "s": 200, "d": 3.0, "speed": 9.0},
        {"cls": "two_wheeler", "policy": "wrong_way",
         "s": 170, "d": 2.4, "speed": 8.0},
        {"cls": "car", "policy": "oncoming", "s": 210, "d": -1.6, "speed": 11.0},
        {"cls": "car", "policy": "oncoming", "s": 160, "d": -2.2, "speed": 10.0},
        {"cls": "car", "policy": "traffic", "s": 40, "d": 1.8, "speed": 10.0},
    ])


def test_reverse_direction_traffic_faces_the_way_it_travels():
    """The bug: a mirrored-frame steer sign inverted the whole lateral loop.

    The rider steered away from its target, saturated, spun, and ended up held
    against the simulator's heading cone - crabbing sideways down the road at a
    mean of 67 degrees to its direction of travel for 90% of every run.
    """
    sim = _reverse_scene()
    watched = {aid: _heading_error_from_reverse for aid, policy
               in sim.policies.items()
               if getattr(policy, "direction", 1) < 0}
    assert len(watched) == 4, "expected two wrong-way riders and two oncoming"

    for aid, errors in _run(sim, watched).items():
        assert len(errors) > 40, f"agent {aid} left the road too early to judge"
        errors = np.array(errors)
        assert errors.mean() < math.radians(25.0), (
            f"agent {aid} travels at a mean {math.degrees(errors.mean()):.0f} "
            f"deg to its own direction of travel")
        # Pinned at the cone is the specific signature of the inverted steer:
        # the policy commands a turn it can never satisfy, and _hold_heading
        # clamps it there for ever.
        pinned = float((errors > HEADING_CONE - math.radians(1.0)).mean())
        assert pinned == 0.0, f"agent {aid} is pinned at the heading cone"


def test_oncoming_traffic_keeps_to_the_half_the_ego_is_not_on():
    """The second half of the same bug, which the first half was hiding.

    ``_mirror_context`` mirrored the *ego's* preferred offset, which names the
    ego's own half of the road. The inverted steer cancelled it, so oncoming
    traffic landed on the correct side for the wrong reason - and fixing the
    steer alone would have driven every oncoming vehicle into the ego's lane.
    """
    sim = _reverse_scene()
    oncoming = [aid for aid, p in sim.policies.items()
                if type(p).__name__ == "OncomingPolicy"]
    forward = [aid for aid, p in sim.policies.items()
               if type(p).__name__ == "TrafficPolicy"]
    tracks = _run(sim, {aid: _offset for aid in oncoming + forward})

    for aid in oncoming:
        assert np.mean(tracks[aid]) < -0.5, (
            f"oncoming agent {aid} sits at d={np.mean(tracks[aid]):+.2f}, "
            f"on the ego's own half of the road")
    for aid in forward:
        assert np.mean(tracks[aid]) > 0.5, (
            f"with-flow agent {aid} sits at d={np.mean(tracks[aid]):+.2f}")


def test_wrong_way_riders_hug_the_ego_side_verge():
    """What makes a wrong-way rider the hazard it is: it is on *your* side."""
    sim = _reverse_scene()
    riders = [aid for aid, p in sim.policies.items()
              if type(p).__name__ == "WrongWayPolicy"]
    tracks = _run(sim, {aid: _offset for aid in riders})
    for aid in riders:
        mean_d = float(np.mean(tracks[aid]))
        assert mean_d > 1.0, f"rider {aid} is not on the ego's half (d={mean_d:+.2f})"


def test_opposing_offset_is_the_other_half_of_the_road():
    """Unit-level companion to the closed-loop test above."""
    centreline = np.column_stack([np.linspace(0, 120, 241), np.zeros(241)])
    c = Corridor.from_spec(centreline, [(0, 4.0, 4.0), (120, 4.0, 4.0)],
                           road_type="two_way")
    assert float(c.nominal_offset(60.0)) > 0.0        # India: ego keeps left
    assert float(c.opposing_offset(60.0)) < 0.0       # so oncoming keeps right
    # A one-way road has no opposing half to keep to.
    one_way = Corridor.from_spec(centreline, [(0, 4.0, 4.0), (120, 4.0, 4.0)],
                                 road_type="one_way")
    assert float(one_way.opposing_offset(60.0)) == \
        pytest.approx(float(one_way.nominal_offset(60.0)))


# -- animals --------------------------------------------------------------
#: Seeds pooled by the statistical assertions below. A single animal's mode
#: sequence is legitimately noisy - a cow can chain "amble" and "stand" draws
#: for a whole run and never once lie down - so a per-cow, single-seed
#: threshold would be a flaky test rather than a strict one. What the claim
#: "cattle behave like cattle" actually means is a property of the herd.
ANIMAL_SEEDS = (9, 17, 23)


def _animal_scene(seed):
    return _scene([
        {"cls": "cattle", "policy": "cattle", "s": 60, "d": 1.0},
        {"cls": "cattle", "policy": "cattle", "s": 90, "d": 2.2,
         "args": {"wander": 0.9, "stop_bias": 0.45}},
        {"cls": "cattle", "policy": "cattle", "s": 130, "d": -0.5},
        {"cls": "stray_dog", "policy": "cattle", "s": 110, "d": 0.8},
    ], duration=30.0, seed=seed)


def _animal_tracks(seed):
    """``(sim, {agent id: [(speed, resting, d), ...]})`` for one seeded run."""
    sim = _animal_scene(seed)
    watched = {aid: (lambda s, a: (float(a.state.speed),
                                   bool(a.memory.get("resting")),
                                   _offset(s, a)))
               for aid, agent in sim.agents.items()
               if agent.cls in (AgentClass.CATTLE, AgentClass.STRAY_DOG)}
    return sim, _run(sim, watched)


def _pooled(cls):
    """Every sample from every animal of ``cls``, across all the seeds."""
    speeds, resting, spans = [], [], []
    for seed in ANIMAL_SEEDS:
        sim, tracks = _animal_tracks(seed)
        for aid, samples in tracks.items():
            if not samples or sim.agents[aid].cls is not cls:
                continue
            speeds += [v for v, _, _ in samples]
            resting += [r for _, r, _ in samples]
            spans.append(max(d for _, _, d in samples) -
                         min(d for _, _, d in samples))
    return np.array(speeds), np.array(resting), spans


def test_cattle_spend_most_of_their_time_not_moving():
    """The bug: a herd that never once stood still, and ran when it moved.

    ``CattlePolicy`` drove its speed error off ``ctx.s_dot`` - the component
    along the *road* - while ``step_holonomic`` integrates ``state.speed``, the
    component along the animal's own body. A cow standing across the
    carriageway reads ``s_dot`` near zero whatever it is really doing, so
    "stand still" was read as "you are already stopped, carry on" and commanded
    full acceleration on every tick. Measured stationary in 1-5% of ticks
    against a 45% stop bias, and pinned at the 2.56 m/s ceiling.
    """
    speeds, _, _ = _pooled(AgentClass.CATTLE)
    still = float((speeds < 0.15).mean())
    assert still > 0.30, f"cattle are stationary only {still:.0%} of the time"
    # A cow ambles. It does not trot, and it certainly does not run.
    assert speeds.max() < 1.6, \
        f"a cow reaches {speeds.max():.2f} m/s, faster than a cow walks"


def test_cattle_sit_down_in_the_road_but_stray_dogs_do_not():
    """Lying down is the canonical hazard; a dog stops, looks, and is gone."""
    _, cow_resting, _ = _pooled(AgentClass.CATTLE)
    assert float(cow_resting.mean()) > 0.15, \
        f"cattle settle only {cow_resting.mean():.0%} of the time"
    _, dog_resting, _ = _pooled(AgentClass.STRAY_DOG)
    assert dog_resting.size and float(dog_resting.mean()) == 0.0, \
        "a stray dog should never lie down in the road"


def test_cattle_still_wander_across_the_carriageway():
    """The other half of the fix: a herd frozen solid is no hazard either.

    The cattle scenario is built on animals drifting out of the verge and into
    the road, so the stationary behaviour above must not have been bought by
    nailing them to the spot.
    """
    _, _, spans = _pooled(AgentClass.CATTLE)
    assert max(spans) > 1.0, \
        f"no cow moved laterally more than {max(spans):.2f} m in any run"


def test_animals_stay_on_the_carriageway():
    """A cow wandering across a field makes every other behaviour look arbitrary."""
    for seed in ANIMAL_SEEDS:
        sim, tracks = _animal_tracks(seed)
        for aid, samples in tracks.items():
            if not samples:
                continue
            worst = max(abs(d) for _, _, d in samples)
            assert worst < HALF_WIDTH + 0.6, (
                f"animal {aid} reached d={worst:.2f} m, off a "
                f"{HALF_WIDTH:.1f} m half-road (seed {seed})")
