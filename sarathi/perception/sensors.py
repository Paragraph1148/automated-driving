"""Sensor models: camera, LiDAR and radar.

The problem statement asks for a multi-sensor setup, but the reason to model
sensors properly is not compliance - it is that **planning against ground truth is
the single most common way a student demo lies about its own capability**. A
planner that has never seen a missed detection, a late track, or a cow hidden
behind a bus is not a planner, it is a replay.

Each sensor here has the failure mode that actually matters for its modality:

* **Camera** - classifies, but misses small and distant objects, and its position
  estimate degrades with range. It is also the only sensor that can tell a cow from
  a motorcycle, which is what the risk field needs.
* **LiDAR** - accurate geometry, no class label, hard occlusion shadow.
* **Radar** - long range and a direct range-rate measurement, but poor lateral
  resolution, so it locates a target well in depth and badly across it.

Occlusion is shared: an agent hidden behind a bus is invisible to all three.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..core.types import Agent, AgentClass


@dataclass
class Detection:
    """A single sensor return."""

    x: float
    y: float
    sensor: str
    #: Radar reports range-rate; camera and LiDAR do not measure velocity directly.
    vx: float | None = None
    vy: float | None = None
    #: Only the camera classifies. ``None`` means "something is there".
    cls: AgentClass | None = None
    cls_confidence: float = 0.0
    #: Positional standard deviation of this return, metres.
    sigma: float = 0.5
    #: Ground-truth id. Present for evaluation and debug rendering ONLY - the
    #: fusion layer and everything downstream must never read it.
    truth_id: int = -1


@dataclass
class SensorSuite:
    """Camera + LiDAR + radar mounted on the ego."""

    camera_range: float = 70.0
    camera_fov: float = math.radians(120.0)
    lidar_range: float = 55.0
    radar_range: float = 120.0
    radar_fov: float = math.radians(40.0)
    #: Scales every miss rate and noise term. Dust, glare, rain, night.
    visibility: float = 1.0
    seed: int = 0

    def __post_init__(self) -> None:
        self.rng = np.random.default_rng(self.seed)

    def sense(self, ego: Agent, agents: list[Agent]) -> list[Detection]:
        vis = compute_visibility(ego, agents)
        out: list[Detection] = []
        for agent, visible_fraction in vis:
            if visible_fraction <= 0.02:
                continue
            out.extend(self._camera(ego, agent, visible_fraction))
            out.extend(self._lidar(ego, agent, visible_fraction))
            out.extend(self._radar(ego, agent, visible_fraction))
        return out

    # -- per-modality -----------------------------------------------------
    def _camera(self, ego: Agent, agent: Agent, visible: float) -> list[Detection]:
        rng_m, bearing = _range_bearing(ego, agent)
        if rng_m > self.camera_range or abs(bearing) > self.camera_fov / 2:
            return []
        p = (_class_detectability(agent.cls)
             * _range_falloff(rng_m, self.camera_range)
             * visible * self.visibility)
        if self.rng.random() > p:
            return []
        # Monocular error is strongly anisotropic: bearing is measured directly
        # and stays accurate, while depth is inferred and degrades roughly with the
        # square of range. Modelling it as isotropic (the easy mistake) scatters
        # detections sideways by metres and fragments every track.
        sigma_depth = 0.30 + 0.008 * rng_m + 0.0009 * rng_m * rng_m
        sigma_lat = 0.15 + 0.004 * rng_m
        ed = float(self.rng.normal(0.0, sigma_depth))
        el = float(self.rng.normal(0.0, sigma_lat))
        heading_to = math.atan2(agent.state.y - ego.state.y,
                                agent.state.x - ego.state.x)
        c, s = math.cos(heading_to), math.sin(heading_to)
        cls, conf = self._classify(agent.cls, rng_m, visible)
        return [Detection(
            x=agent.state.x + ed * c - el * s,
            y=agent.state.y + ed * s + el * c,
            sensor="camera", cls=cls, cls_confidence=conf,
            sigma=max(sigma_depth, sigma_lat), truth_id=agent.id)]

    def _lidar(self, ego: Agent, agent: Agent, visible: float) -> list[Detection]:
        rng_m, _ = _range_bearing(ego, agent)
        if rng_m > self.lidar_range:
            return []
        # LiDAR needs enough returns on a surface; small objects far away vanish.
        p = float(np.clip(0.99 * visible * self.visibility
                          * _range_falloff(rng_m, self.lidar_range)
                          * (0.55 + 0.45 * min(1.0, agent.params.width / 1.2)),
                          0.0, 1.0))
        if self.rng.random() > p:
            return []
        sigma = 0.05 + 0.004 * rng_m
        return [Detection(
            x=agent.state.x + float(self.rng.normal(0.0, sigma)),
            y=agent.state.y + float(self.rng.normal(0.0, sigma)),
            sensor="lidar", sigma=sigma, truth_id=agent.id)]

    def _radar(self, ego: Agent, agent: Agent, visible: float) -> list[Detection]:
        rng_m, bearing = _range_bearing(ego, agent)
        if rng_m > self.radar_range or abs(bearing) > self.radar_fov / 2:
            return []
        # Radar cross-section: a truck returns strongly, a pedestrian barely.
        rcs = {AgentClass.PEDESTRIAN: 0.30, AgentClass.STRAY_DOG: 0.15,
               AgentClass.CATTLE: 0.35, AgentClass.BICYCLE: 0.40,
               AgentClass.TWO_WHEELER: 0.55, AgentClass.PUSHCART: 0.35}.get(
                   agent.cls, 0.95)
        if self.rng.random() > rcs * visible * self.visibility:
            return []
        # Good in depth, poor across it: noise is anisotropic in the sensor frame.
        sigma_r, sigma_az = 0.25, 0.035 * rng_m
        er = float(self.rng.normal(0.0, sigma_r))
        ea = float(self.rng.normal(0.0, sigma_az))
        heading_to = math.atan2(agent.state.y - ego.state.y,
                                agent.state.x - ego.state.x)
        c, s = math.cos(heading_to), math.sin(heading_to)
        v = agent.state.velocity
        return [Detection(
            x=agent.state.x + er * c - ea * s,
            y=agent.state.y + er * s + ea * c,
            sensor="radar",
            vx=float(v[0] + self.rng.normal(0.0, 0.25)),
            vy=float(v[1] + self.rng.normal(0.0, 0.9)),
            sigma=max(sigma_r, sigma_az), truth_id=agent.id)]

    def _classify(self, true_cls: AgentClass, rng_m: float,
                  visible: float) -> tuple[AgentClass, float]:
        """Class label with realistic, *structured* confusion.

        Confusions are not uniform: a two-wheeler is mistaken for a bicycle, a cow
        for a handcart. Getting these wrong in a plausible way matters, because the
        risk field is class-conditioned and an auto-rickshaw mislabelled as a car
        is a much smaller error than a cow mislabelled as a barricade.
        """
        confusion = {
            AgentClass.TWO_WHEELER: [AgentClass.BICYCLE, AgentClass.AUTO_RICKSHAW],
            AgentClass.BICYCLE: [AgentClass.TWO_WHEELER, AgentClass.PEDESTRIAN],
            AgentClass.AUTO_RICKSHAW: [AgentClass.CAR, AgentClass.TWO_WHEELER],
            AgentClass.CAR: [AgentClass.AUTO_RICKSHAW],
            AgentClass.BUS: [AgentClass.TRUCK],
            AgentClass.TRUCK: [AgentClass.BUS],
            AgentClass.CATTLE: [AgentClass.PUSHCART, AgentClass.PARKED_VEHICLE],
            AgentClass.PUSHCART: [AgentClass.CATTLE, AgentClass.BARRICADE],
            AgentClass.STRAY_DOG: [AgentClass.PEDESTRIAN],
            AgentClass.PEDESTRIAN: [AgentClass.BICYCLE],
        }
        quality = _range_falloff(rng_m, self.camera_range) * visible
        conf = float(np.clip(0.55 + 0.42 * quality, 0.35, 0.98))
        alternatives = confusion.get(true_cls, [])
        if alternatives and self.rng.random() > conf:
            return alternatives[int(self.rng.integers(len(alternatives)))], \
                float(np.clip(conf * 0.8, 0.3, 0.9))
        return true_cls, conf


# -- occlusion ------------------------------------------------------------
def compute_visibility(ego: Agent,
                       agents: list[Agent]) -> list[tuple[Agent, float]]:
    """Fraction of each agent's angular extent not hidden by something nearer.

    Angular-interval occlusion rather than ray casting: project every footprint to
    a bearing span, sort by range, and accumulate the shadow. It is O(n log n) and
    exact enough for a bird's-eye sensor model, which matters because this runs
    every tick of every Monte-Carlo run.
    """
    ego_p = ego.state.position
    entries = []
    for agent in agents:
        if agent.id == ego.id or not agent.active:
            continue
        corners = agent.corners()
        rel = corners - ego_p
        ranges = np.linalg.norm(rel, axis=1)
        bearings = np.arctan2(rel[:, 1], rel[:, 0])
        lo, hi = _bearing_span(bearings)
        entries.append((float(ranges.min()), lo, hi, agent))

    entries.sort(key=lambda e: e[0])
    shadow: list[tuple[float, float]] = []
    out: list[tuple[Agent, float]] = []
    for _, lo, hi, agent in entries:
        span = hi - lo
        if span <= 1e-9:
            out.append((agent, 1.0))
            shadow = _merge_interval(shadow, lo, hi)
            continue
        # Compare against the shadow in all three wrap frames, so an occluder
        # straddling +/-pi still shadows correctly.
        covered = 0.0
        for shift in (-2 * np.pi, 0.0, 2 * np.pi):
            covered += sum(max(0.0, min(hi, b + shift) - max(lo, a + shift))
                           for a, b in shadow)
        out.append((agent, float(np.clip(1.0 - covered / span, 0.0, 1.0))))
        shadow = _merge_interval(shadow, lo, hi)
    return out


def _bearing_span(bearings: np.ndarray) -> tuple[float, float]:
    """Smallest bearing interval covering all corners, unwrapped across +/-pi.

    The corners are sorted and the *largest* angular gap between consecutive
    bearings is treated as the empty side; the occupied arc is everything else. If
    that largest gap is the one spanning +/-pi the arc needs no unwrapping at all -
    getting this branch wrong inflates a 3-degree bus into a 365-degree occluder
    that shadows the entire scene.
    """
    b = np.sort(bearings)
    n = len(b)
    gaps = np.diff(np.concatenate([b, b[:1] + 2 * np.pi]))
    k = int(np.argmax(gaps))
    lo = float(b[(k + 1) % n])
    hi = float(b[k])
    if hi < lo:
        hi += 2 * np.pi
    return lo, hi


def _merge_interval(intervals: list[tuple[float, float]], lo: float,
                    hi: float) -> list[tuple[float, float]]:
    """Insert ``[lo, hi]`` into a sorted, disjoint interval list."""
    merged = sorted(intervals + [(lo, hi)])
    out: list[tuple[float, float]] = []
    for a, b in merged:
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _range_bearing(ego: Agent, agent: Agent) -> tuple[float, float]:
    rel = agent.state.position - ego.state.position
    rng_m = float(np.linalg.norm(rel))
    bearing = math.atan2(rel[1], rel[0]) - ego.state.heading
    return rng_m, float((bearing + math.pi) % (2 * math.pi) - math.pi)


def _range_falloff(r: float, max_range: float) -> float:
    """Smooth detection quality falloff, 1 near the sensor to 0 at max range."""
    u = float(np.clip(r / max(max_range, 1e-6), 0.0, 1.0))
    return float((1.0 - u) ** 0.6)


def _class_detectability(cls: AgentClass) -> float:
    """Base camera detection rate by class. Small dark objects are genuinely hard."""
    return {
        AgentClass.BUS: 0.99, AgentClass.TRUCK: 0.99, AgentClass.CAR: 0.98,
        AgentClass.AUTO_RICKSHAW: 0.96, AgentClass.PARKED_VEHICLE: 0.97,
        AgentClass.TWO_WHEELER: 0.93, AgentClass.CATTLE: 0.90,
        AgentClass.BICYCLE: 0.88, AgentClass.PUSHCART: 0.85,
        AgentClass.PEDESTRIAN: 0.86, AgentClass.BARRICADE: 0.82,
        AgentClass.STRAY_DOG: 0.68,
    }.get(cls, 0.90)
