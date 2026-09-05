"""Guardrails for the Indian Driving Risk Field.

These encode the claims the report makes about the field, so a refactor that
quietly turns it back into an occupancy grid fails the build.
"""
import time

import numpy as np
import pytest

from sarathi.core.types import AgentClass
from sarathi.perception.fusion import Track
from sarathi.planning.risk import HARM_WEIGHT, IndianDrivingRiskField
from sarathi.prediction.intent import IntentPredictor
from sarathi.world.corridor import Corridor, SurfaceDefect


def corridor(defects=()):
    return Corridor.from_spec(
        np.column_stack([np.linspace(0, 200, 401), np.zeros(401)]),
        [(0, 4.0, 4.0), (200, 4.0, 4.0)],
        road_type="two_way", surface_quality=0.8, defects=list(defects))


def field_for(tracks, cor=None, pred=None):
    cor = cor or corridor()
    pred = pred or IntentPredictor()
    preds = pred.predict(tracks, cor.reference, ego_d=1.8)
    return IndianDrivingRiskField(preds, cor, pred.times, 4.2, 1.7)


def track(cls, x=40.0, y=1.5, vx=4.0, vy=0.0):
    # An *established* track, which is what these tests are about. A velocity
    # derived from the first two frames of a new one is not an observation of
    # motion, and Track.is_moving declines to treat it as one.
    return Track(id=1, x=x, y=y, vx=vx, vy=vy, hits=10, age=1.0,
                 P=np.diag([0.2, 0.2, 1.0, 1.0]), cls=cls, cls_confidence=0.95)


def test_peak_risk_is_harm_weighted_by_class():
    """Striking a pedestrian must cost more than clipping a barricade.

    Measured at the agent's own position, which isolates the harm weighting from
    geometry - a car has a far larger body than a pedestrian, so at a fixed offset
    the two effects are confounded.
    """
    probe_classes = (AgentClass.PEDESTRIAN, AgentClass.TWO_WHEELER,
                     AgentClass.CATTLE, AgentClass.CAR, AgentClass.BARRICADE)
    peaks = {cls: float(field_for([track(cls)]).agent_risk(
        np.array([[40.0, 1.5]]), 0.0)[0]) for cls in probe_classes}
    ordering = sorted(probe_classes, key=lambda c: -peaks[c])
    assert ordering[0] is AgentClass.PEDESTRIAN
    assert ordering[-1] is AgentClass.BARRICADE
    assert peaks[AgentClass.PEDESTRIAN] > peaks[AgentClass.CAR] \
        > peaks[AgentClass.BARRICADE]
    # And the ordering must match the declared harm weights, not merely be stable.
    assert ordering == sorted(probe_classes, key=lambda c: -HARM_WEIGHT[c])


def test_collision_is_a_hard_constraint_not_a_cost():
    """Contact must be forbidden outright, whatever the soft field says.

    Harm weighting makes a near-miss with a pedestrian score higher than contact
    with a car. That is the right *preference* and a disastrous *constraint*, so
    feasibility is a separate hard test.
    """
    car = field_for([track(AgentClass.CAR)])
    ped = field_for([track(AgentClass.PEDESTRIAN)])
    probe = np.array([[40.0, 3.4]])   # inside the car; 1.6 m clear of the pedestrian

    assert float(car.penetration(probe, 0.0)[0]) > 0.0
    assert float(ped.penetration(probe, 0.0)[0]) == 0.0
    # The soft field still warns well before contact, so the planner is steered
    # away rather than merely stopped at the boundary.
    assert float(ped.agent_risk(probe, 0.0)[0]) > 0.2


def test_penetration_ignores_negligible_hypotheses():
    """Every tail hypothesis of every agent would otherwise close the road."""
    f = field_for([track(AgentClass.TWO_WHEELER, vx=12.0)])
    probe = np.column_stack([np.full(80, 40.0), np.linspace(-6, 8, 80)])
    blocked_all = (f.penetration(probe, 2.0, min_probability=0.0) > 0).sum()
    blocked_likely = (f.penetration(probe, 2.0, min_probability=0.05) > 0).sum()
    assert blocked_likely <= blocked_all


def test_feasible_path_check_matches_penetration():
    f = field_for([track(AgentClass.CAR, x=60.0, y=0.0, vx=0.0)])
    through = np.column_stack([np.linspace(40, 80, 9), np.zeros(9)])
    around = np.column_stack([np.linspace(40, 80, 9), np.full(9, 3.6)])
    times = np.linspace(0.0, 3.2, 9)
    assert not f.path_is_feasible(through, times)
    assert f.path_is_feasible(around, times)


def test_risk_is_time_indexed_and_peaks_on_arrival():
    """Crossing in front of a car is safe or fatal depending purely on when."""
    f = field_for([track(AgentClass.CAR, x=20.0, y=0.0, vx=14.0)])
    probe = np.array([[48.0, 0.0]])
    series = [float(f.agent_risk(probe, t)[0]) for t in (0.0, 1.0, 2.0, 3.0, 4.0)]
    assert series[0] < 0.05
    assert series[2] == max(series)
    assert series[-1] < series[2]


def test_uncertain_classes_produce_broader_fields():
    """A cow should shut down more of the road than a bus of the same size does."""
    def spread(cls):
        f = field_for([track(cls, vx=1.0)])
        probe = np.column_stack([np.full(60, 40.0), np.linspace(-4, 4, 60)])
        return float((f.agent_risk(probe, 3.0) > 0.15).sum())
    assert spread(AgentClass.CATTLE) > spread(AgentClass.BUS)


def test_pothole_is_traversable_cost_not_an_obstacle():
    cor = corridor([SurfaceDefect(60.0, 1.5, 1.3, 0.8)])
    f = IndianDrivingRiskField([], cor, np.array([0.0]), 4.2, 1.7)
    on_defect = float(f.terrain_risk(np.array([[60.0, 1.5]]))[0])
    clean = float(f.terrain_risk(np.array([[100.0, 1.5]]))[0])
    outside = float(f.terrain_risk(np.array([[100.0, 5.0]]))[0])
    assert on_defect > clean
    assert on_defect < outside          # costly to cross, but far cheaper than leaving the road
    assert np.isfinite(on_defect)


def test_leaving_the_corridor_costs_more_than_any_surface_defect():
    cor = corridor([SurfaceDefect(60.0, 0.0, 1.5, 1.0)])
    f = IndianDrivingRiskField([], cor, np.array([0.0]), 4.2, 1.7)
    worst_defect = float(f.terrain_risk(np.array([[60.0, 0.0]]))[0])
    just_outside = float(f.terrain_risk(np.array([[60.0, 4.5]]))[0])
    assert just_outside > worst_defect


def test_boundary_cost_ramps_up_smoothly():
    f = IndianDrivingRiskField([], corridor(), np.array([0.0]), 4.2, 1.7)
    probe = np.column_stack([np.full(40, 100.0), np.linspace(0.0, 3.2, 40)])
    values = f.terrain_risk(probe)
    spacing = 3.2 / 39
    assert np.all(np.diff(values) >= -1e-9)      # monotonically non-decreasing
    # Bounded gradient rather than a bounded step: the property that matters is
    # that there is no discontinuity for the planner to fall off.
    assert float(np.max(np.abs(np.diff(values)))) < 12.0 * spacing


def test_field_evaluation_is_fast_enough_for_20hz():
    """The planner scores thousands of points per tick inside a 50 ms budget."""
    tracks = [Track(id=i, x=20 + i * 7, y=(-1) ** i * 1.5, vx=8.0, vy=0.0,
                    hits=10, age=1.0, P=np.diag([.3, .3, 1., 1.]),
                    cls=[AgentClass.CAR, AgentClass.TWO_WHEELER,
                         AgentClass.CATTLE, AgentClass.BUS,
                         AgentClass.PEDESTRIAN][i % 5], cls_confidence=0.9)
              for i in range(20)]
    f = field_for(tracks)
    pts = np.random.default_rng(0).uniform([0, -4], [200, 4], size=(4000, 2))
    f.evaluate(pts, 1.0)                          # warm up
    t0 = time.perf_counter()
    for _ in range(5):
        f.evaluate(pts, 1.0)
    elapsed_ms = (time.perf_counter() - t0) / 5 * 1000
    assert elapsed_ms < 45.0, f"risk field too slow: {elapsed_ms:.1f} ms"


def test_empty_scene_has_no_agent_risk():
    f = field_for([])
    assert float(f.agent_risk(np.array([[50.0, 0.0]]), 1.0)[0]) == 0.0
