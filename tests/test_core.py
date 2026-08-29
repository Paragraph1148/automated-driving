"""Numerical guardrails for the geometry, Frenet and kinematics layers."""
import math

import numpy as np
import pytest

from sarathi.core.frenet import ReferencePath
from sarathi.core.geom import (convex_polygons_intersect, point_in_polygon,
                               points_in_polygon, polygon_distance,
                               polyline_curvature, polyline_arclength,
                               project_point_to_polyline)
from sarathi.core.kinematics import step_bicycle, step_holonomic, wheelbase_for
from sarathi.core.types import AgentClass, State, params_for


def straight(length=200.0, n=401):
    return np.column_stack([np.linspace(0, length, n), np.zeros(n)])


def arc(radius=40.0, sweep=math.pi / 2, n=500):
    th = np.linspace(0, sweep, n)
    return np.column_stack([radius * np.sin(th), radius * (1 - np.cos(th))])


def test_projection_sign_is_positive_to_the_left():
    pts = straight()
    s = polyline_arclength(pts)
    _, d_left, _ = project_point_to_polyline(pts, s, np.array([30.0, 4.0]))
    _, d_right, _ = project_point_to_polyline(pts, s, np.array([30.0, -4.0]))
    assert d_left == pytest.approx(4.0, abs=1e-6)
    assert d_right == pytest.approx(-4.0, abs=1e-6)


def test_curvature_matches_analytic_circle():
    pts = arc(radius=50.0)
    k = polyline_curvature(pts, polyline_arclength(pts))
    assert float(np.median(k)) == pytest.approx(1.0 / 50.0, rel=1e-3)


def test_frenet_round_trip_on_curve():
    rp = ReferencePath(arc(), smooth_window=0)
    for s_in, d_in in [(10.0, 2.0), (30.0, -3.0), (55.0, 1.5)]:
        s_out, d_out = rp.to_frenet(rp.to_cartesian(s_in, d_in))
        assert s_out == pytest.approx(s_in, abs=0.05)
        assert d_out == pytest.approx(d_in, abs=0.02)


def test_state_to_frenet_decomposes_velocity():
    rp = ReferencePath(straight())
    st = State(50.0, 3.0, math.radians(10), 10.0)
    _, d, s_dot, d_dot = rp.state_to_frenet(st)
    assert d == pytest.approx(3.0, abs=1e-6)
    assert s_dot == pytest.approx(10 * math.cos(math.radians(10)), abs=1e-6)
    assert d_dot == pytest.approx(10 * math.sin(math.radians(10)), abs=1e-6)


def test_bicycle_constant_steer_traces_exact_circle():
    p = params_for(AgentClass.CAR)
    L = wheelbase_for(p)
    delta = math.radians(20)
    expected_R = L / math.tan(delta)
    st = State(0, 0, 0, 5.0)
    pts = []
    for _ in range(2000):
        st = step_bicycle(st, 0.0, delta, 0.01, L, 20.0)
        pts.append((st.x, st.y))
    radii = np.linalg.norm(np.array(pts) - np.array([0.0, expected_R]), axis=1)
    assert radii.mean() == pytest.approx(expected_R, rel=1e-3)
    assert radii.std() < 1e-3


def test_bicycle_respects_speed_limits():
    p = params_for(AgentClass.CAR)
    st = State(0, 0, 0, 0.0)
    for _ in range(400):
        st = step_bicycle(st, 5.0, 0.0, 0.05, wheelbase_for(p), v_max=12.0)
    assert st.speed == pytest.approx(12.0)


def test_holonomic_agent_turns_toward_resultant_velocity():
    st = State(0, 0, 0, 1.0)
    for _ in range(40):
        st = step_holonomic(st, 0.0, 0.5, 0.05, 3.0, 1.0)
    assert st.y > 0.5 and st.heading > 0.5


def test_sat_and_distance():
    r1 = np.array([[0, 0], [2, 0], [2, 1], [0, 1]], float)
    r2 = np.array([[1.5, .5], [3, .5], [3, 1.5], [1.5, 1.5]], float)
    r3 = np.array([[5, 5], [6, 5], [6, 6], [5, 6]], float)
    assert convex_polygons_intersect(r1, r2)
    assert not convex_polygons_intersect(r1, r3)
    assert polygon_distance(r1, r2) == 0.0
    assert polygon_distance(r1, r3) == pytest.approx(5.0, abs=1e-6)


def test_point_in_polygon_matches_vectorised():
    poly = np.array([[0, 0], [4, 0], [4, 2], [0, 2]], float)
    pts = np.array([[1.0, 1.0], [9.0, 1.0], [3.9, 0.1], [-1.0, 1.0]])
    expected = [point_in_polygon(p, poly) for p in pts]
    assert points_in_polygon(pts, poly).tolist() == expected
