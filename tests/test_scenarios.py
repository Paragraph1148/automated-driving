"""Integration guardrails for the five required scenarios.

These are cheap but they catch the two failure modes that silently invalidate
everything downstream: a scenario that no longer loads, and traffic that spawns
inside the ego (which turns a planner benchmark into a spawn-collision benchmark).
"""
import glob

import numpy as np
import pytest

from sarathi.core.geom import polygon_distance
from sarathi.core.types import Agent, AgentClass, State
from sarathi.planning.baseline import BaselineLaneFollower
from sarathi.sim.simulator import EGO_ID, Simulator
from sarathi.world.scenario import load_scenario, populate

REQUIRED = [
    "scenarios/village_road_unmarked.yaml",
    "scenarios/urban_intersection_unsignalled.yaml",
    "scenarios/highway_merge_slow.yaml",
    "scenarios/market_dense_mixed.yaml",
    "scenarios/cattle_crossing_sudden.yaml",
]


def test_all_five_required_scenarios_exist():
    """The PS names exactly these five; losing one is an instant mark deduction."""
    found = set(glob.glob("scenarios/*.yaml"))
    assert set(REQUIRED) <= found


@pytest.mark.parametrize("path", REQUIRED)
def test_scenario_loads_and_is_self_consistent(path):
    sc = load_scenario(path)
    assert sc.corridor.reference.length > 50.0
    assert 0.0 <= sc.chaos <= 1.0
    assert sc.goal_s <= sc.corridor.reference.length
    assert sc.ego["s"] < sc.goal_s
    assert "required" in sc.tags
    assert float(sc.corridor.width_at(sc.ego["s"])) > 2.0


@pytest.mark.parametrize("path", REQUIRED)
def test_nothing_spawns_inside_anything_else(path):
    sc = load_scenario(path)
    agents, _ = populate(sc, np.random.default_rng(sc.seed))
    corners = {a.id: a.corners() for a in agents.values()}
    ids = sorted(corners)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            assert polygon_distance(corners[a], corners[b]) > 0.0, \
                f"{path}: agents {a} and {b} spawned overlapping"


@pytest.mark.parametrize("path", REQUIRED)
def test_ego_starts_with_clear_road(path):
    """A scenario that starts the ego 1 m behind a stopped bus measures nothing."""
    sc = load_scenario(path)
    agents, _ = populate(sc, np.random.default_rng(sc.seed))
    ref = sc.corridor.reference
    pos = ref.to_cartesian(sc.ego["s"], sc.ego["d"])
    ego = Agent(EGO_ID, AgentClass.CAR,
                State(float(pos[0]), float(pos[1]),
                      float(ref.heading_at(sc.ego["s"])), sc.ego["speed"]))
    clearances = [polygon_distance(ego.corners(), a.corners())
                  for a in agents.values()]
    assert min(clearances) > 1.5


@pytest.mark.parametrize("path", REQUIRED)
def test_scenario_runs_closed_loop_and_is_deterministic(path):
    sc = load_scenario(path)
    sc.duration = 12.0                       # keep the suite fast
    a = Simulator(sc, BaselineLaneFollower()).run().metrics
    b = Simulator(load_scenario(path), BaselineLaneFollower())
    b.scenario.duration = 12.0
    b = b.run().metrics
    assert a.sim_time > 0.0
    assert a.replan_count > 0
    assert a.distance == pytest.approx(b.distance, rel=1e-9)
    assert a.goal_progress == pytest.approx(b.goal_progress, rel=1e-9)


def test_chaos_increases_hazard_population():
    """The chaos slider must actually deform the scene, monotonically."""
    counts = []
    for chaos in (0.0, 0.5, 1.0):
        sc = load_scenario(REQUIRED[0], chaos=chaos, seed=3)
        agents, policies = populate(sc, np.random.default_rng(3))
        hazards = sum(1 for aid, (name, _) in policies.items()
                      if name in ("wrong_way", "cattle", "rash"))
        counts.append(hazards)
    assert counts[0] < counts[1] < counts[2], counts


def test_lane_markings_degrade_with_chaos():
    clean = load_scenario("scenarios/cattle_crossing_sudden.yaml", chaos=0.0)
    messy = load_scenario("scenarios/cattle_crossing_sudden.yaml", chaos=1.0)
    assert clean.corridor.lane_marking_quality > messy.corridor.lane_marking_quality
    assert messy.corridor.lane_marking_quality == pytest.approx(0.0)
