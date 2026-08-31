"""Behavioural guardrails for NLB-IDM and the corridor.

These assert the *emergent behaviours* we claim in the report, so a regression that
quietly turns Indian traffic back into orderly lane-following fails the build.
"""
import numpy as np
import pytest

from sarathi.agents.nlbidm import (Neighbour, lateral_accel, longitudinal_accel,
                                   pseudo_lane_halfwidth, separation_ratio)
from sarathi.core.types import AgentClass, params_for
from sarathi.world.corridor import Corridor, SurfaceDefect


def car_nb(s, d, s_dot=0.0):
    return Neighbour(1, AgentClass.CAR, s, d, s_dot, 0.0, 2.1, 0.85)


def test_free_road_accelerates_and_settles_at_desired_speed():
    p = params_for(AgentClass.CAR)
    a_rest, _ = longitudinal_accel(AgentClass.CAR, p, 1.0, 0., 0., 0.0, [])
    a_cruise, _ = longitudinal_accel(AgentClass.CAR, p, 1.0, 0., 0., p.v_desired, [])
    assert a_rest == pytest.approx(p.a_max)
    assert a_cruise == pytest.approx(0.0, abs=1e-9)


def test_leader_influence_decays_smoothly_with_lateral_offset():
    """The centreline separation ratio, which lane-based models get as a step."""
    p = params_for(AgentClass.CAR)
    accels = []
    for off in (0.0, 2.5, 3.5, 5.0):
        a, _ = longitudinal_accel(AgentClass.CAR, p, 1.0, 0., 0., 12.0,
                                  [car_nb(25.0, off, 6.0)])
        accels.append(a)
    assert accels == sorted(accels)          # monotonically less restrictive
    assert accels[0] < -2.0                  # directly ahead: real braking
    assert accels[-1] > -0.1                 # 5 m offset: essentially free


def test_separation_ratio_is_continuous_and_bounded():
    xs = np.linspace(0.0, 6.0, 200)
    vals = [separation_ratio(0.0, 0.85, x, 0.35, 2.0) for x in xs]
    assert max(vals) <= 1.0 and min(vals) >= 0.0
    assert max(abs(np.diff(vals))) < 0.05    # no step discontinuity
    assert vals[0] == 1.0 and vals[-1] == 0.0


def test_pseudo_lane_widens_with_speed():
    p = params_for(AgentClass.TWO_WHEELER)
    assert pseudo_lane_halfwidth(p, 15.0) > pseudo_lane_halfwidth(p, 0.0) + 0.5


def test_head_on_agent_provokes_emergency_braking():
    """A wrong-way rider closes at the sum of speeds and must dominate."""
    p = params_for(AgentClass.CAR)
    onc = Neighbour(2, AgentClass.TWO_WHEELER, 30.0, 0.2, -12.0, 0., 0.95, 0.35)
    a, gov = longitudinal_accel(AgentClass.CAR, p, 1.0, 0., 0., 12.0, [onc])
    assert a < -3.0 and gov == 2


def test_two_wheeler_filters_but_bus_does_not():
    """The signature Indian behaviour: gap filling is class-specific."""
    blockers = [car_nb(12.0, 0.0, 2.0)]
    a_2w = lateral_accel(AgentClass.TWO_WHEELER, params_for(AgentClass.TWO_WHEELER),
                         1.2, 0., 0., 10., 0., blockers, 0., -4., 4.)
    a_bus = lateral_accel(AgentClass.BUS, params_for(AgentClass.BUS),
                          1.0, 0., 0., 10., 0., blockers, 0., -4., 4.)
    assert abs(a_2w) > 1.0
    assert abs(a_bus) < 0.2


def test_abreast_vehicle_repels_but_distant_leader_does_not():
    p = params_for(AgentClass.CAR)
    abreast = lateral_accel(AgentClass.CAR, p, 1.0, 0., 0., 10., 0.,
                            [car_nb(1.0, 0.9, 10.0)], 0., -4., 4.)
    ahead = lateral_accel(AgentClass.CAR, p, 1.0, 0., 0., 10., 0.,
                          [car_nb(30.0, 0.0, 10.0)], 0., -4., 4.)
    assert abreast < -1.0                    # pushed right, away from the neighbour
    assert abs(ahead) < 0.5                  # no swerving at range


def test_gap_seeking_ignores_same_speed_traffic_but_overtakes_slower():
    """Weaving around a vehicle doing your own speed is a modelling artefact."""
    p = params_for(AgentClass.CAR)
    same = lateral_accel(AgentClass.CAR, p, 1.0, 0., 0., 10., 0.,
                         [car_nb(25.0, 0.0, 10.0)], 0., -4., 4.)
    slower = lateral_accel(AgentClass.CAR, p, 1.0, 0., 0., 10., 0.,
                           [car_nb(25.0, 0.0, 3.0)], 0., -4., 4.)
    assert abs(same) < 0.2
    assert abs(slower) > 0.5


def test_lateral_command_returns_to_preferred_offset():
    p = params_for(AgentClass.CAR)
    a = lateral_accel(AgentClass.CAR, p, 1.0, 0., 2.0, 10., 0., [], 0., -4., 4.)
    assert a < 0.0


def _corridor():
    return Corridor.from_spec(
        np.column_stack([np.linspace(0, 120, 241), np.zeros(241)]),
        [(0, 4.0, 4.0), (60, 2.5, 2.5), (120, 5.0, 5.0)],
        road_type="two_way", surface_quality=0.35,
        defects=[SurfaceDefect(40.0, 1.5, 1.2, 0.8)])


def test_corridor_width_interpolates_and_bounds_are_signed():
    c = _corridor()
    assert float(c.width_at(0.0)) == pytest.approx(8.0)
    assert float(c.width_at(60.0)) == pytest.approx(5.0)
    d_min, d_max = c.bounds_at(60.0)
    assert float(d_min) == pytest.approx(-2.5) and float(d_max) == pytest.approx(2.5)


def test_corridor_containment_and_margin():
    c = _corridor()
    assert c.contains(np.array([60.0, 2.0]))
    assert not c.contains(np.array([60.0, 3.5]))
    assert c.lateral_margin(np.array([60.0, 1.0])) == pytest.approx(1.5, abs=1e-6)


def test_pothole_is_traversable_cost_not_a_wall():
    """A pothole must slow the vehicle, never make the cell impassable."""
    c = _corridor()
    on_hole = float(c.defect_cost(np.array([40.0, 1.5]))[0])
    clear = float(c.defect_cost(np.array([80.0, 0.0]))[0])
    assert on_hole > clear
    assert np.isfinite(on_hole)
    assert c.comfortable_speed(np.array([40.0, 1.5]), 13.9) < \
           c.comfortable_speed(np.array([80.0, 0.0]), 13.9)


def test_keep_left_nominal_offset_is_on_the_left_half():
    c = _corridor()
    assert float(c.nominal_offset(60.0)) > 0.0
