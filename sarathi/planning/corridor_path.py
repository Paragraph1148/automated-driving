"""Virtual reference path derivation.

Frenet planning is the right tool for this problem, and it needs a reference
curve. Every production stack takes that curve from the lane centreline - which is
exactly the thing an unmarked Indian village road does not have, and which a faded
or wrongly-painted marking supplies incorrectly, which is worse.

SARATHI derives the reference from the **drivable corridor** instead. Given the
corridor bounds and whatever is standing still inside them (parked vehicles,
barricades, a handcart, a cow that has decided to stop), a short dynamic program
finds the lateral profile that stays clear of obstructions, keeps left where it
can, and bends as little as possible. That profile *is* the reference path.

The consequence is the central claim of the project: because the planner never
consumed lane markings, it does not degrade when they are absent. Markings, when
present, are only ever a prior on the nominal offset.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..core.frenet import ReferencePath
from ..world.corridor import Corridor


@dataclass
class StaticBlockage:
    """Something stationary occupying part of the corridor."""

    s: float
    d: float
    half_length: float
    half_width: float
    #: How strongly to avoid it. Scaled by harm weight upstream.
    weight: float = 1.0


@dataclass
class CorridorPathConfig:
    horizon: float = 70.0          # m of road to plan the reference over
    s_step: float = 2.5            # longitudinal resolution
    d_step: float = 0.3            # lateral resolution
    keep_left_weight: float = 0.35   # pull toward the nominal side of the road
    smoothness_weight: float = 5.0   # penalty on lateral rate of change
    blockage_weight: float = 60.0    # penalty for overlapping something static
    edge_weight: float = 25.0        # penalty for approaching the corridor edge
    #: Maximum lateral movement per metre travelled. Caps reference curvature so
    #: the path we hand the lattice is one the vehicle can actually track.
    max_lateral_rate: float = 0.28


@dataclass
class ReferenceSolution:
    """The derived reference path and what it cost to find it."""

    path: ReferencePath
    s_grid: np.ndarray
    d_profile: np.ndarray
    #: Free lateral space either side of the chosen line, at its tightest point in
    #: the near field. This - not deviation from the nominal offset - is what tells
    #: the behaviour layer we are squeezing past something. Deviation is a bad
    #: signal because the nominal offset itself moves whenever the road changes
    #: width, so a highway taper reads as a permanent obstruction.
    clearance: float = float("inf")
    #: Deviation from the nominal offset, kept for reporting and diagnostics.
    deviation: float = 0.0


def derive_reference_path(corridor: Corridor, ego_s: float, ego_d: float,
                          blockages: list[StaticBlockage],
                          ego_half_width: float,
                          config: CorridorPathConfig | None = None,
                          near_horizon: float = 25.0) -> ReferenceSolution:
    """Build a reference path through the free space ahead of the ego."""
    cfg = config or CorridorPathConfig()
    ref = corridor.reference

    s_end = min(ref.length, ego_s + cfg.horizon)
    n_s = max(2, int(round((s_end - ego_s) / cfg.s_step)) + 1)
    s_grid = np.linspace(ego_s, s_end, n_s)

    d_min, d_max = corridor.bounds_at(s_grid)
    lo = float(np.min(d_min)) - 0.5
    hi = float(np.max(d_max)) + 0.5
    n_d = max(3, int(round((hi - lo) / cfg.d_step)) + 1)
    d_grid = np.linspace(lo, hi, n_d)

    node_cost = _node_costs(corridor, s_grid, d_grid, blockages,
                            ego_half_width, cfg)

    # Transition cost penalises lateral rate of change, and forbids anything
    # sharper than the vehicle can track.
    delta = d_grid[None, :] - d_grid[:, None]                 # (from, to)
    max_shift = cfg.max_lateral_rate * cfg.s_step
    transition = cfg.smoothness_weight * (delta / cfg.s_step) ** 2
    transition[np.abs(delta) > max_shift] = np.inf

    # Start pinned to where the ego actually is.
    total = np.full(n_d, np.inf)
    start = int(np.argmin(np.abs(d_grid - ego_d)))
    total[start] = node_cost[0, start]
    back = np.zeros((n_s, n_d), dtype=int)

    for i in range(1, n_s):
        candidate = total[:, None] + transition
        back[i] = np.argmin(candidate, axis=0)
        total = candidate[back[i], np.arange(n_d)] + node_cost[i]

    profile = np.empty(n_s, dtype=int)
    profile[-1] = int(np.argmin(total))
    for i in range(n_s - 1, 0, -1):
        profile[i - 1] = back[i, profile[i]]
    d_profile = d_grid[profile]

    xy = _to_world(ref, s_grid, d_profile)
    path = ReferencePath(xy, spacing=0.5, smooth_window=7)

    near = s_grid <= ego_s + near_horizon
    clearance = _path_clearance(corridor, s_grid[near], d_profile[near],
                                blockages, ego_half_width)
    nominal = np.asarray(corridor.nominal_offset(s_grid))
    deviation = float(np.max(np.abs(d_profile - nominal)))
    return ReferenceSolution(path, s_grid, d_profile, clearance, deviation)


def _path_clearance(corridor: Corridor, s_grid: np.ndarray, d_profile: np.ndarray,
                    blockages: list[StaticBlockage],
                    ego_half_width: float) -> float:
    """Smallest free lateral gap either side of the chosen line."""
    if len(s_grid) == 0:
        return float("inf")
    d_min, d_max = corridor.bounds_at(s_grid)
    left = (d_max - ego_half_width) - d_profile
    right = d_profile - (d_min + ego_half_width)
    clearance = np.minimum(left, right)

    for blk in blockages:
        overlaps = np.abs(s_grid - blk.s) < blk.half_length + 2.0
        if not np.any(overlaps):
            continue
        gap = (np.abs(d_profile - blk.d) - blk.half_width - ego_half_width)
        clearance = np.where(overlaps, np.minimum(clearance, gap), clearance)
    return float(np.min(clearance))


def _node_costs(corridor: Corridor, s_grid: np.ndarray, d_grid: np.ndarray,
                blockages: list[StaticBlockage], ego_half_width: float,
                cfg: CorridorPathConfig) -> np.ndarray:
    """Cost of occupying each (s, d) cell."""
    n_s, n_d = len(s_grid), len(d_grid)
    d_min, d_max = corridor.bounds_at(s_grid)

    # Staying on the ego's own side of the road, softly.
    nominal = np.asarray(corridor.nominal_offset(s_grid)).reshape(-1, 1)
    cost = cfg.keep_left_weight * (d_grid[None, :] - nominal) ** 2

    # Corridor edges: a ramp that starts biting before the verge and climbs
    # steeply beyond it, so the path prefers to stay on the road but can run wide
    # briefly if that is the only way past an obstruction.
    slack = np.minimum(d_grid[None, :] - (d_min[:, None] + ego_half_width),
                       (d_max[:, None] - ego_half_width) - d_grid[None, :])
    cost = cost + cfg.edge_weight * np.maximum(0.0, -slack) ** 2
    cost = cost + cfg.edge_weight * 0.08 * np.maximum(0.0, 0.8 - slack) ** 2

    # Static blockages, as rectangles inflated by the ego's half width.
    for blk in blockages:
        ds = np.abs(s_grid[:, None] - blk.s) - blk.half_length
        dd = np.abs(d_grid[None, :] - blk.d) - (blk.half_width + ego_half_width)
        overlap = (np.maximum(0.0, -ds) > 0.0) & (np.maximum(0.0, -dd) > 0.0)
        # A soft skirt outside the rectangle keeps the path from grazing it.
        near = np.exp(-0.5 * (np.maximum(0.0, ds) / 3.0) ** 2) * \
               np.exp(-0.5 * (np.maximum(0.0, dd) / 0.7) ** 2)
        cost = cost + cfg.blockage_weight * blk.weight * (overlap * 1.0 + 0.35 * near)

    return cost


def _to_world(ref: ReferencePath, s: np.ndarray, d: np.ndarray) -> np.ndarray:
    base = ref.point_at(s)
    normal = ref.normal_at(s)
    return base + d[:, None] * normal


def blockages_from_tracks(tracks, corridor: Corridor,
                          speed_threshold: float = 0.6,
                          s_min: float = -20.0,
                          ego_speed: float = 0.0,
                          cruise_speed: float = 12.0) -> list[StaticBlockage]:
    """Extract obstructions that the reference path should route around.

    Anything *materially slower than we want to travel* counts, not merely anything
    stationary. This is the difference between a planner that drives on an Indian
    road and one that queues on it: a handcart at 1 m/s and a cycle at 3 m/s are
    obstructions to be passed, and treating only stopped objects as blockages
    leaves the vehicle stuck behind the slowest thing in front of it forever.

    The local planner still treats every one of them as dynamic and able to move;
    this only shapes where the reference line goes.
    """
    threshold = max(speed_threshold, 0.45 * cruise_speed)
    from ..planning.risk import HARM_WEIGHT

    out: list[StaticBlockage] = []
    for tr in tracks:
        if tr.speed > threshold:
            continue
        # Weight the obstruction by how much it would cost us: something almost
        # keeping pace is barely worth deviating for.
        slowness = float(np.clip(1.0 - tr.speed / max(threshold, 1e-3), 0.15, 1.0))
        s, d = corridor.reference.to_frenet(tr.position)
        if s < s_min:
            continue
        out.append(StaticBlockage(
            s=float(s), d=float(d),
            half_length=tr.length / 2.0, half_width=tr.width / 2.0,
            weight=slowness * float(HARM_WEIGHT.get(tr.cls, 0.6)) / 0.6))
    return out
