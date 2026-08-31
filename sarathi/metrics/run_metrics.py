"""Metrics for a single run.

The problem statement names three: replanning latency, path smoothness and
scenario completion rate. Those are necessary but not sufficient - a planner can
score well on all three by crawling. So we also record safety margins (minimum
time-to-collision, minimum clearance broken down by road-user class, and
post-encroachment time) and efficiency, and report them together.

Every number here comes from the simulator, never from a human reading a plot.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..core.types import Agent, AgentClass
from ..core.geom import polygon_distance

#: Contact below this relative speed is recorded as a low-speed CONTACT rather than
#: a CRASH. Both count as run failures - the distinction is for diagnosis, not for
#: letting a planner off.
CONTACT_SPEED_THRESHOLD = 0.5


@dataclass
class RunMetrics:
    scenario: str = ""
    seed: int = 0
    chaos: float = 0.0

    # Outcome
    completed: bool = False
    collision: bool = False
    collision_with: str | None = None
    #: Relative speed at the moment of contact, m/s. Reported separately because a
    #: pedestrian brushing a stationary vehicle at 0.13 m/s and striking a cow at
    #: 12 m/s are not the same failure, and averaging them hides both.
    impact_speed: float = 0.0
    #: Our own speed at the moment of contact, and where the other body was
    #: relative to our nose (0 deg ahead, +-180 behind). Together these separate
    #: "we drove into it" from "it drove into us while we were stopped", which
    #: a raw collision count cannot, and which changes what the fix should be.
    ego_speed_at_impact: float = 0.0
    impact_bearing_deg: float = 0.0
    timed_out: bool = False
    left_corridor: bool = False

    # Progress / efficiency
    sim_time: float = 0.0
    distance: float = 0.0
    goal_progress: float = 0.0
    mean_speed: float = 0.0

    # Safety
    min_ttc: float = float("inf")
    min_clearance: float = float("inf")
    min_clearance_by_class: dict[str, float] = field(default_factory=dict)
    hard_brake_events: int = 0

    # Comfort / smoothness (the PS "path smoothness")
    jerk_rms: float = 0.0
    lat_accel_p95: float = 0.0
    steering_reversals: int = 0
    curvature_rms: float = 0.0

    # Real-time behaviour (the PS "replanning latency")
    replan_ms_mean: float = 0.0
    replan_ms_p50: float = 0.0
    replan_ms_p95: float = 0.0
    replan_ms_p99: float = 0.0
    replan_ms_max: float = 0.0
    replan_count: int = 0

    def as_row(self) -> dict:
        d = dict(self.__dict__)
        d["min_clearance_by_class"] = dict(self.min_clearance_by_class)
        return d

    def summary(self) -> str:
        if self.collision:
            status = "CRASH" if self.impact_speed >= CONTACT_SPEED_THRESHOLD \
                else "CONTACT"
        elif self.completed:
            status = "COMPLETE"
        elif self.left_corridor:
            status = "OFF-ROAD"
        else:
            status = "STUCK"
        extra = f" @{self.impact_speed:.1f}m/s" if self.collision else ""
        return (f"{self.scenario:<30s} chaos={self.chaos:.2f} seed={self.seed:<3d} "
                f"{status:<8s}{extra:<10s} progress={self.goal_progress*100:5.1f}% "
                f"minTTC={self.min_ttc:5.2f}s minClr={self.min_clearance:4.2f}m "
                f"jerkRMS={self.jerk_rms:5.2f} p95lat={self.replan_ms_p95:6.2f}ms")


class MetricsAccumulator:
    """Streams per-tick state into the aggregates above."""

    def __init__(self, scenario: str, seed: int, chaos: float, goal_s: float):
        self.m = RunMetrics(scenario=scenario, seed=seed, chaos=chaos)
        self.goal_s = goal_s
        self._accels: list[float] = []
        self._lat_accels: list[float] = []
        self._curvatures: list[float] = []
        self._steers: list[float] = []
        self._speeds: list[float] = []
        self._latencies: list[float] = []
        self._prev_xy: np.ndarray | None = None
        self._dt = 0.05

    def tick(self, dt: float, ego: Agent, others: list[Agent],
             steer: float, latency_ms: float | None) -> None:
        self._dt = dt
        st = ego.state
        self._accels.append(st.accel)
        self._speeds.append(st.speed)
        self._steers.append(steer)
        self._lat_accels.append(abs(st.speed * st.yaw_rate))
        self._curvatures.append(st.yaw_rate / max(st.speed, 0.5))
        if latency_ms is not None:
            self._latencies.append(latency_ms)
        if st.accel < -0.75 * ego.params.b_max:
            self.m.hard_brake_events += 1

        xy = st.position
        if self._prev_xy is not None:
            self.m.distance += float(np.linalg.norm(xy - self._prev_xy))
        self._prev_xy = xy.copy()
        self.m.sim_time += dt

        self._update_safety(ego, others)

    def _update_safety(self, ego: Agent, others: list[Agent]) -> None:
        ego_poly = ego.corners()
        ego_p = ego.state.position
        ego_v = ego.state.velocity
        ego_r = ego.footprint.radius()

        for other in others:
            if not other.active:
                continue
            rel = other.state.position - ego_p
            broad = float(np.linalg.norm(rel))
            if broad > 60.0:
                continue

            clearance = polygon_distance(ego_poly, other.corners())
            if clearance < self.m.min_clearance:
                self.m.min_clearance = clearance
            key = other.cls.value
            prev = self.m.min_clearance_by_class.get(key, float("inf"))
            if clearance < prev:
                self.m.min_clearance_by_class[key] = clearance

            ttc = time_to_collision(ego, other)
            if ttc < self.m.min_ttc:
                self.m.min_ttc = ttc

    def finalise(self, completed: bool, collision: bool,
                 collision_with: str | None, left_corridor: bool,
                 ego_s: float, impact_speed: float = 0.0,
                 ego_speed_at_impact: float = 0.0,
                 impact_bearing_deg: float = 0.0) -> RunMetrics:
        m = self.m
        m.completed = completed
        m.collision = collision
        m.collision_with = collision_with
        m.impact_speed = float(impact_speed)
        m.ego_speed_at_impact = float(ego_speed_at_impact)
        m.impact_bearing_deg = float(impact_bearing_deg)
        m.left_corridor = left_corridor
        m.timed_out = not (completed or collision or left_corridor)
        m.goal_progress = float(np.clip(ego_s / max(self.goal_s, 1e-6), 0.0, 1.0))
        m.mean_speed = float(np.mean(self._speeds)) if self._speeds else 0.0

        if len(self._accels) > 1:
            jerk = np.diff(self._accels) / max(self._dt, 1e-6)
            m.jerk_rms = float(np.sqrt(np.mean(jerk ** 2)))
        if self._lat_accels:
            m.lat_accel_p95 = float(np.percentile(self._lat_accels, 95))
        if self._curvatures:
            m.curvature_rms = float(np.sqrt(np.mean(np.square(self._curvatures))))
        m.steering_reversals = _count_reversals(self._steers)

        if self._latencies:
            lat = np.asarray(self._latencies)
            m.replan_count = int(lat.size)
            m.replan_ms_mean = float(lat.mean())
            m.replan_ms_p50 = float(np.percentile(lat, 50))
            m.replan_ms_p95 = float(np.percentile(lat, 95))
            m.replan_ms_p99 = float(np.percentile(lat, 99))
            m.replan_ms_max = float(lat.max())

        if not np.isfinite(m.min_clearance):
            m.min_clearance = float("nan")
        return m


_DISC_CACHE: dict[tuple[float, float], tuple[np.ndarray, float]] = {}


def disc_decomposition(length: float, width: float) -> tuple[np.ndarray, float]:
    """Cover a vehicle rectangle with a small number of equal discs.

    A single circumscribing disc is far too coarse here. On an Indian road vehicles
    routinely pass within a metre of each other, and a circumscribed car (radius
    2.3 m) plus a circumscribed two-wheeler (1.0 m) overlap during every ordinary
    overtake - which pins minimum TTC at exactly zero for the whole run and destroys
    the metric. Splitting the body along its length into ``n`` discs of radius
    ``hypot(L/2n, W/2)`` keeps the test conservative while staying informative.

    Returns ``(longitudinal offsets from the centre, disc radius)``.
    """
    key = (round(length, 3), round(width, 3))
    cached = _DISC_CACHE.get(key)
    if cached is not None:
        return cached
    n = 1 if length <= 1.6 else (2 if length <= 3.2 else 3)
    radius = math.hypot(length / (2.0 * n), width / 2.0)
    offsets = (np.arange(n) - (n - 1) / 2.0) * (length / n)
    _DISC_CACHE[key] = (offsets, radius)
    return offsets, radius


def _disc_centres(agent: Agent) -> tuple[np.ndarray, float]:
    offsets, radius = disc_decomposition(agent.params.length, agent.params.width)
    st = agent.state
    axis = np.array([math.cos(st.heading), math.sin(st.heading)])
    return st.position[None, :] + offsets[:, None] * axis[None, :], radius


def time_to_collision(ego: Agent, other: Agent) -> float:
    """Constant-velocity TTC between two multi-disc vehicle bodies.

    Rotation is ignored: over the sub-second horizons where TTC is actually a
    useful signal, the translation term dominates.
    """
    ego_c, ego_r = _disc_centres(ego)
    oth_c, oth_r = _disc_centres(other)
    rel_vel = other.state.velocity - ego.state.velocity
    radius = ego_r + oth_r

    best = float("inf")
    for i in range(len(ego_c)):
        rel = oth_c - ego_c[i][None, :]
        for j in range(len(oth_c)):
            t = _time_to_collision(rel[j], rel_vel, radius)
            if t < best:
                best = t
                if best == 0.0:
                    return 0.0
    return best


def _time_to_collision(rel_pos: np.ndarray, rel_vel: np.ndarray,
                       radius: float) -> float:
    """TTC of two discs, solving |p + v t| = R for the smallest positive root.

    Disc approximation is deliberate: it is conservative, cheap enough to run every
    tick against every neighbour, and TTC is only ever used as a safety *margin*.
    """
    a = float(rel_vel @ rel_vel)
    if a < 1e-9:
        return float("inf")
    b = 2.0 * float(rel_pos @ rel_vel)
    c = float(rel_pos @ rel_pos) - radius * radius
    if c <= 0.0:
        return 0.0                       # already overlapping
    disc = b * b - 4.0 * a * c
    if disc < 0.0:
        return float("inf")              # closest approach never touches
    root = (-b - math.sqrt(disc)) / (2.0 * a)
    return root if root > 0.0 else float("inf")


def _count_reversals(steers: list[float], deadband: float = 0.02) -> int:
    """Sign changes in the steering command - a proxy for hunting/oscillation."""
    count = 0
    last_sign = 0
    for s in steers:
        if abs(s) < deadband:
            continue
        sign = 1 if s > 0 else -1
        if last_sign and sign != last_sign:
            count += 1
        last_sign = sign
    return count
