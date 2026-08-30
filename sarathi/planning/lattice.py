"""Frenet lattice local planner.

Samples a fan of jerk-minimal candidate trajectories around the virtual reference
path, scores each against the risk field in space *and time*, discards the
infeasible ones, and returns the best.

Two details matter more than the sampling itself:

* The reference is the **corridor-derived** path, not a lane centreline, so the
  whole lattice is already positioned in free space rather than in a lane that may
  not exist. Lateral sampling around it therefore stays modest.
* Candidates are scored at each point's *own* time. Scoring against a frozen
  snapshot of the world is the classic way to produce a plan that drives
  confidently into where a bus is about to be.

The whole fan is built as batched arrays rather than candidate-by-candidate. With
135 candidates of 17 samples each, the per-candidate version spent most of its
time in NumPy call overhead on 17-element arrays and missed the 20 Hz budget
outright.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..core.frenet import ReferencePath
from .risk import IndianDrivingRiskField


#: How far outside the corridor edge a trajectory may stray before it is
#: rejected. Matches the simulator's own off-road termination tolerance.
OFF_ROAD_TOLERANCE = 0.2


@dataclass
class LatticeConfig:
    #: Lateral offsets sampled either side of the reference, metres. This is the
    #: span at speed; the span actually used is scaled by how far the vehicle will
    #: travel during the manoeuvre - see ``max_lateral_rate``.
    lateral_span: float = 2.4
    lateral_samples: int = 9
    #: Lateral displacement achievable per metre travelled forward. A car cannot
    #: translate sideways, so offering a 2.4 m offset to a vehicle that will cover
    #: 2.7 m during the manoeuvre demands a curvature fifty times its limit and
    #: every such candidate is correctly rejected - leaving the planner with
    #: nothing. Scaling the span by predicted travel keeps the fan feasible at
    #: every speed, which is what lets the vehicle pull away from a standstill.
    max_lateral_rate: float = 0.30
    #: Smallest lateral span ever sampled, so the fan never collapses to a line.
    min_lateral_span: float = 0.35
    #: Durations over which a lateral manoeuvre completes, seconds.
    durations: tuple[float, ...] = (1.6, 2.6, 3.6)
    #: Fractions of the behaviour layer's target speed to sample.
    speed_fractions: tuple[float, ...] = (0.0, 0.35, 0.7, 1.0, 1.15)
    #: Absolute terminal speeds always sampled in addition to the fractions. Without
    #: these, a target speed of zero makes every candidate a decelerate-to-standstill
    #: trajectory and the vehicle can never creep away from a stop again.
    speed_absolutes: tuple[float, ...] = (0.0, 1.5, 3.0)
    #: Trajectory sampling for scoring.
    horizon: float = 4.0
    n_samples: int = 17

    # Comfort and feasibility limits.
    #: Must match the vehicle's actual comfortable acceleration. Sampling beyond
    #: it wastes candidates; sampling below it makes the car sluggish.
    a_long_max: float = 2.0
    #: Must not be tighter than the vehicle's actual emergency braking, or the
    #: hardest stop the car can perform is rejected as infeasible.
    b_long_max: float = 7.0
    a_lat_max: float = 4.0
    curvature_max: float = 0.28        # ~3.6 m turning radius
    jerk_max: float = 12.0

    #: Probability above which a manoeuvre hypothesis is treated as a *hard*
    #: obstacle. Everything below this shapes the soft risk field only.
    #:
    #: This threshold matters more than any cost weight. At 0.05 the planner
    #: treats a two-wheeler's 20%-likely cut-in as certain; with a dozen agents
    #: each carrying several hypotheses, the union of everything anyone *might*
    #: do covers the whole road and the vehicle stops permanently. Hard
    #: constraints are for what is nearly certain; the rest belongs in the cost.
    hard_constraint_probability: float = 0.5

    # Cost weights.
    w_risk: float = 12.0
    w_jerk_lat: float = 0.14
    w_jerk_long: float = 0.10
    w_speed: float = 2.4
    w_offset: float = 1.1
    w_terminal_offset: float = 2.0


@dataclass
class Candidate:
    """One sampled trajectory, in both Frenet and world coordinates."""

    times: np.ndarray
    s: np.ndarray
    d: np.ndarray
    s_dot: np.ndarray
    xy: np.ndarray
    heading: np.ndarray
    curvature: np.ndarray
    speed: np.ndarray
    target_d: float
    target_speed: float
    duration: float
    cost: float = np.inf
    risk: float = 0.0
    feasible: bool = True
    reject_reason: str = ""
    extras: dict = field(default_factory=dict)


def quintic(x0, v0, a0, x1, v1, a1, T):
    """Coefficients of the jerk-minimal quintic meeting both end states."""
    T = max(T, 1e-3)
    A = np.array([[T ** 3, T ** 4, T ** 5],
                  [3 * T ** 2, 4 * T ** 3, 5 * T ** 4],
                  [6 * T, 12 * T ** 2, 20 * T ** 3]])
    b = np.array([x1 - (x0 + v0 * T + 0.5 * a0 * T ** 2),
                  v1 - (v0 + a0 * T),
                  a1 - a0])
    c3, c4, c5 = np.linalg.solve(A, b)
    return np.array([x0, v0, 0.5 * a0, c3, c4, c5])


def quartic(x0, v0, a0, v1, a1, T):
    """Jerk-minimal quartic that meets a terminal *velocity* rather than position."""
    T = max(T, 1e-3)
    A = np.array([[3 * T ** 2, 4 * T ** 3],
                  [6 * T, 12 * T ** 2]])
    b = np.array([v1 - (v0 + a0 * T), a1 - a0])
    c3, c4 = np.linalg.solve(A, b)
    return np.array([x0, v0, 0.5 * a0, c3, c4])


def _poly_eval(coeffs: np.ndarray, t: np.ndarray, order: int = 0) -> np.ndarray:
    """Evaluate a polynomial (or its derivative) over ``t``.

    ``coeffs`` may be (C,) for one polynomial or (P, C) for a batch, in which case
    ``t`` is (P, T) and the result is (P, T).
    """
    c = np.atleast_2d(coeffs)
    for _ in range(order):
        if c.shape[1] <= 1:
            return np.zeros_like(t)
        c = c[:, 1:] * np.arange(1, c.shape[1])[None, :]
    powers = np.stack([t ** i for i in range(c.shape[1])], axis=-1)
    return np.einsum("...c,...c->...", c[:, None, :] if powers.ndim == 3 else c,
                     powers)


class FrenetLatticePlanner:
    """Sample, score, and select a local trajectory."""

    def __init__(self, config: LatticeConfig | None = None,
                 ego_half_width: float = 0.85):
        self.cfg = config or LatticeConfig()
        self._ego_half_width = ego_half_width

    def plan(self, reference: ReferencePath, state: tuple[float, float, float,
                                                          float, float, float],
             target_speed: float, risk: IndianDrivingRiskField,
             d_bias: float = 0.0,
             margin_relief: float = 0.0,
             lateral_limits: tuple[float, float] | None = None
             ) -> tuple[Candidate | None, list[Candidate]]:
        """Plan from Frenet state ``(s, s_dot, s_ddot, d, d_dot, d_ddot)``.

        Returns ``(best, all_candidates)``. All candidates are returned so the
        Mission Control view can draw the whole fan, which is the single most
        legible way to show what the planner is actually considering.
        """
        cfg = self.cfg
        times = np.linspace(0.0, cfg.horizon, cfg.n_samples)
        # Seed the polynomials from a *moderate* initial acceleration. Feeding a
        # -7 m/s^2 emergency decel in as an initial condition forces the quintic
        # to swing violently to unwind it, and the whole fan fails feasibility.
        s0, s_dot0, s_ddot0, d0, d_dot0, d_ddot0 = state
        state = (s0, s_dot0, float(np.clip(s_ddot0, -3.0, 2.0)),
                 d0, d_dot0, float(np.clip(d_ddot0, -2.0, 2.0)))
        batch = self._sample(state, target_speed, d_bias, times, lateral_limits)
        self._lift(batch, reference, times)
        self._feasibility(batch, times)
        self._score(batch, risk, times, target_speed, margin_relief)

        candidates = self._materialise(batch, times)

        # A guaranteed-available fallback: brake in a straight line along the
        # current reference offset. Constructed kinematically rather than as a
        # polynomial, so it satisfies the acceleration and curvature limits by
        # construction and can never itself be rejected as infeasible.
        fallback = self._safe_stop(state, reference, times, risk, margin_relief)
        candidates.append(fallback)

        # The fallback is a guarantee, never a competitor. Letting it be selected
        # on cost is fatal: from a standstill no sampled trajectory can reach the
        # target speed, so every one of them carries a large speed-error penalty,
        # the flat-cost fallback wins, and the vehicle never moves again.
        feasible = [c for c in candidates
                    if c.feasible and not c.extras.get("safe_stop")]
        if feasible:
            return min(feasible, key=lambda c: c.cost), candidates
        if fallback.feasible:
            return fallback, candidates
        # Nothing is collision-free. Braking on the current line is still the best
        # available action, so hand it back rather than returning nothing.
        fallback.reject_reason = "last resort"
        return fallback, candidates

    def _safe_stop(self, state, reference: ReferencePath, times: np.ndarray,
                   risk: IndianDrivingRiskField,
                   margin_relief: float = 0.0) -> Candidate:
        """Constant-deceleration stop holding the current lateral offset."""
        cfg = self.cfg
        s0, s_dot0, _, d0, _, _ = state
        v0 = max(s_dot0, 0.0)
        decel = cfg.b_long_max * 0.8
        t_stop = v0 / decel if decel > 0 else 0.0
        tc = np.minimum(times, t_stop)
        s = np.clip(s0 + v0 * tc - 0.5 * decel * tc ** 2, 0.0, reference.length)
        s_dot = np.maximum(v0 - decel * tc, 0.0)
        d = np.full_like(times, d0)

        xy, _ = reference.frenet_traj_to_cartesian(s, d, s_dot,
                                                   np.zeros_like(times))
        dt = np.gradient(times)
        dx, dy = np.gradient(xy[:, 0]), np.gradient(xy[:, 1])
        speed = np.hypot(dx / dt, dy / dt)
        ds = np.hypot(dx, dy)
        heading = np.where(ds > 1e-3, np.arctan2(dy, dx),
                           reference.heading_at(s))
        curvature = np.where(ds < 0.02, 0.0,
                             np.gradient(np.unwrap(heading)) / np.maximum(ds, 1e-3))

        cand = Candidate(times=times, s=s, d=d, s_dot=s_dot, xy=xy,
                         heading=heading, curvature=curvature, speed=speed,
                         target_d=float(d0), target_speed=0.0,
                         duration=float(max(t_stop, 1e-3)),
                         extras={"safe_stop": True})
        values = risk.evaluate_path(xy, times, sd=(s, d))
        cand.risk = float(np.trapezoid(values, times) / max(times[-1], 1e-6))
        blocked = any(
            float(risk.penetration(
                xy[i:i + 1], float(times[i]),
                min_probability=cfg.hard_constraint_probability,
                margin_relief=margin_relief)[0]) > 0.0
            for i in range(len(times)))
        c_s, c_d = risk.corridor.to_frenet_batch(xy)
        d_min, d_max = risk.corridor.hard_bounds_at(c_s)
        off = bool(np.any((c_d > d_max - self._ego_half_width + OFF_ROAD_TOLERANCE) |
                          (c_d < d_min + self._ego_half_width - OFF_ROAD_TOLERANCE)))
        cand.feasible = not (blocked or off)
        cand.reject_reason = ("" if cand.feasible
                              else ("off road" if off else "collision"))
        cand.cost = cfg.w_risk * cand.risk
        return cand

    # -- stage 1: sample the lattice, batched -----------------------------
    def _sample(self, state, target_speed, d_bias, times,
                lateral_limits=None) -> dict:
        cfg = self.cfg
        s0, s_dot0, s_ddot0, d0, d_dot0, d_ddot0 = state
        speeds = np.array(sorted({
            round(max(0.0, target_speed * f), 3) for f in cfg.speed_fractions
        } | {
            round(v, 3) for v in cfg.speed_absolutes
        }))

        # Lateral and longitudinal polynomials are independent given the duration,
        # so evaluate each family once and combine by index rather than solving
        # one polynomial per candidate.
        lat_coeffs, lat_key = [], []
        lon_coeffs, lon_key = [], []
        for di, duration in enumerate(cfg.durations):
            # How far the vehicle can travel during this manoeuvre, and hence how
            # far it can move sideways. Bounding the *displacement from where the
            # ego already is* - rather than the absolute offset - is the part that
            # matters: a lateral bias of -0.7 m otherwise pushes every target
            # beyond reach, and the whole fan is rejected on curvature.
            travel = (s_dot0 * duration
                      + 0.35 * cfg.a_long_max * duration ** 2)
            max_shift = float(np.clip(cfg.max_lateral_rate * travel,
                                      cfg.min_lateral_span, cfg.lateral_span))
            desired = np.linspace(-cfg.lateral_span, cfg.lateral_span,
                                  cfg.lateral_samples) + d_bias
            offsets = d0 + np.clip(desired - d0, -max_shift, max_shift)
            if lateral_limits is not None:
                # Generate inside the carriageway rather than generating outside
                # it and discarding afterwards. On a 4.4 m village road the
                # discard-after approach throws away most of the fan and leaves
                # the planner with nothing to choose between.
                lo, hi = lateral_limits
                offsets = np.clip(offsets, min(lo, hi), max(lo, hi))
            offsets = np.unique(np.round(offsets, 3))
            for target_d in offsets:
                lat_coeffs.append(quintic(d0, d_dot0, d_ddot0, float(target_d),
                                          0.0, 0.0, duration))
                lat_key.append((di, float(target_d)))
            for v in speeds:
                lon_coeffs.append(quartic(s0, s_dot0, s_ddot0, float(v), 0.0,
                                          duration))
                lon_key.append((di, float(v)))

        lat_coeffs = np.array(lat_coeffs)
        lon_coeffs = np.array(lon_coeffs)
        durations = np.array(cfg.durations)

        lat_dur = np.array([durations[k[0]] for k in lat_key])
        lon_dur = np.array([durations[k[0]] for k in lon_key])
        t_lat = np.minimum(times[None, :], lat_dur[:, None])
        t_lon = np.minimum(times[None, :], lon_dur[:, None])

        d_all = _poly_eval(lat_coeffs, t_lat)
        d_dot_all = _poly_eval(lat_coeffs, t_lat, 1)
        d_ddot_all = _poly_eval(lat_coeffs, t_lat, 2)
        past = times[None, :] > lat_dur[:, None]
        d_dot_all = np.where(past, 0.0, d_dot_all)
        d_ddot_all = np.where(past, 0.0, d_ddot_all)

        s_all = _poly_eval(lon_coeffs, t_lon)
        s_dot_all = _poly_eval(lon_coeffs, t_lon, 1)
        s_ddot_all = _poly_eval(lon_coeffs, t_lon, 2)
        overrun = np.maximum(0.0, times[None, :] - lon_dur[:, None])
        s_all = s_all + s_dot_all * overrun
        s_ddot_all = np.where(times[None, :] > lon_dur[:, None], 0.0, s_ddot_all)

        # Pair every lateral with every longitudinal *of the same duration*.
        lat_idx, lon_idx = [], []
        for di in range(len(durations)):
            lats = [i for i, k in enumerate(lat_key) if k[0] == di]
            lons = [i for i, k in enumerate(lon_key) if k[0] == di]
            for li in lats:
                for oi in lons:
                    lat_idx.append(li)
                    lon_idx.append(oi)
        lat_idx = np.array(lat_idx)
        lon_idx = np.array(lon_idx)

        return {
            "lat_idx": lat_idx, "lon_idx": lon_idx,
            "d": d_all[lat_idx], "d_dot": d_dot_all[lat_idx],
            "d_ddot": d_ddot_all[lat_idx],
            "s": s_all[lon_idx], "s_dot": s_dot_all[lon_idx],
            "s_ddot": s_ddot_all[lon_idx],
            "target_d": np.array([lat_key[i][1] for i in lat_idx]),
            "target_v": np.array([lon_key[i][1] for i in lon_idx]),
            "duration": np.array([durations[lat_key[i][0]] for i in lat_idx]),
            "feasible": np.ones(len(lat_idx), dtype=bool),
            "reason": [""] * len(lat_idx),
        }

    # -- stage 2: lift to world, batched ----------------------------------
    def _lift(self, batch: dict, reference: ReferencePath,
              times: np.ndarray) -> None:
        m, n = batch["s"].shape
        batch["s"] = np.clip(batch["s"], 0.0, reference.length)
        batch["s_dot"] = np.maximum(batch["s_dot"], 0.0)

        xy, _ = reference.frenet_traj_to_cartesian(
            batch["s"].ravel(), batch["d"].ravel(),
            batch["s_dot"].ravel(), batch["d_dot"].ravel())
        batch["xy"] = xy.reshape(m, n, 2)

        dt = np.gradient(times)[None, :]
        dx = np.gradient(batch["xy"][..., 0], axis=1)
        dy = np.gradient(batch["xy"][..., 1], axis=1)
        speed = np.hypot(dx / dt, dy / dt)
        batch["speed"] = speed

        # Heading from the path's own geometry, not from atan2(d_dot, s_dot).
        # That closed form has a singularity at standstill: as s_dot goes to zero
        # the computed heading swings to +/-90 degrees off the reference, the
        # apparent curvature explodes, and every candidate is rejected as
        # un-driveable precisely when the vehicle is trying to pull away.
        ds = np.hypot(dx, dy)
        ref_heading = reference.heading_at(batch["s"].ravel()).reshape(m, n)
        moving = ds > 1e-3
        heading = np.where(moving, np.arctan2(dy, dx), ref_heading)
        batch["heading"] = heading

        # Curvature is geometric - heading change per metre of path - not a time
        # derivative divided by speed. The latter blows up as the vehicle slows,
        # so a nearly stationary car appears to be cornering impossibly hard.
        curvature = np.gradient(np.unwrap(heading, axis=1), axis=1) / \
            np.maximum(ds, 1e-3)
        batch["curvature"] = np.where(ds < 0.02, 0.0, curvature)

    # -- stage 3: kinematic feasibility -----------------------------------
    def _feasibility(self, batch: dict, times: np.ndarray) -> None:
        cfg = self.cfg
        a_long = batch["s_ddot"]
        a_lat = batch["speed"] ** 2 * np.abs(batch["curvature"])

        checks = [
            (np.any(a_long > cfg.a_long_max + 1e-6, axis=1), "longitudinal accel"),
            (np.any(a_long < -cfg.b_long_max - 1e-6, axis=1), "longitudinal accel"),
            (np.any(np.abs(batch["curvature"]) > cfg.curvature_max, axis=1),
             "curvature"),
            (np.any(a_lat > cfg.a_lat_max, axis=1), "lateral accel"),
        ]
        for mask, reason in checks:
            newly = mask & batch["feasible"]
            batch["feasible"] &= ~mask
            for i in np.flatnonzero(newly):
                batch["reason"][i] = reason

    # -- stage 4: risk scoring --------------------------------------------
    def _score(self, batch: dict, risk: IndianDrivingRiskField,
               times: np.ndarray, target_speed: float,
               margin_relief: float = 0.0) -> None:
        cfg = self.cfg
        live = np.flatnonzero(batch["feasible"])
        batch["risk"] = np.zeros(len(batch["feasible"]))
        batch["cost"] = np.full(len(batch["feasible"]), np.inf)
        if len(live) == 0:
            return

        xy = batch["xy"][live]                       # (L, T, 2)
        s = batch["s"][live]
        d = batch["d"][live]
        values = np.empty(xy.shape[:2])
        blocked = np.zeros(len(live), dtype=bool)

        # Project into *corridor* coordinates for the terrain term. ``s`` and ``d``
        # here are offsets along the derived reference, which is a different curve
        # whenever it deviates around an obstruction - feeding those in made the
        # boundary cost measure the wrong road.
        corridor = risk.corridor
        c_s, c_d = corridor.to_frenet_batch(xy.reshape(-1, 2))
        terrain = risk.terrain_risk(xy.reshape(-1, 2),
                                    sd=(c_s, c_d)).reshape(xy.shape[:2])

        # Leaving the carriageway is a hard constraint, not a cost. The simulator
        # ends a run for it, so the planner must not be able to buy its way out
        # of the corridor by paying a finite penalty.
        d_min, d_max = corridor.hard_bounds_at(c_s)
        half_w = self._ego_half_width
        outside = ((c_d > d_max - half_w + OFF_ROAD_TOLERANCE) |
                   (c_d < d_min + half_w - OFF_ROAD_TOLERANCE)).reshape(xy.shape[:2])
        leaves_road = outside.any(axis=1)
        for ti, t_value in enumerate(times):
            pts = xy[:, ti, :]
            values[:, ti] = risk.agent_risk(pts, float(t_value))
            blocked |= risk.penetration(
                pts, float(t_value),
                min_probability=cfg.hard_constraint_probability,
                margin_relief=margin_relief) > 0.0
        values = np.minimum(values + terrain, risk.cfg.risk_cap)

        for i, idx in enumerate(live):
            if leaves_road[i]:
                batch["feasible"][idx] = False
                batch["reason"][idx] = "off road"
            elif blocked[i]:
                batch["feasible"][idx] = False
                batch["reason"][idx] = "collision"

        keep = ~(blocked | leaves_road)
        if not np.any(keep):
            return
        idx = live[keep]
        horizon = max(times[-1], 1e-6)
        mean_risk = np.trapezoid(values[keep], times, axis=1) / horizon

        jerk_lat = np.gradient(batch["d_ddot"][idx], times, axis=1)
        jerk_long = np.gradient(batch["s_ddot"][idx], times, axis=1)
        # Normalised, so the penalty for falling short of 12 m/s is not five times
        # the penalty for falling short of 2.4 m/s. An absolute penalty makes the
        # planner disproportionately reckless at speed and timid when crawling.
        speed_error = (np.abs(batch["target_v"][idx] - target_speed)
                       / max(target_speed, 1.5))

        batch["risk"][idx] = mean_risk
        batch["cost"][idx] = (
            cfg.w_risk * mean_risk
            + cfg.w_jerk_lat * np.sqrt(np.mean(jerk_lat ** 2, axis=1))
            + cfg.w_jerk_long * np.sqrt(np.mean(jerk_long ** 2, axis=1))
            + cfg.w_speed * speed_error
            + cfg.w_offset * np.mean(np.abs(batch["d"][idx]), axis=1)
            + cfg.w_terminal_offset * np.abs(batch["target_d"][idx]))

    # -- stage 5: hand back objects ---------------------------------------
    def _materialise(self, batch: dict, times: np.ndarray) -> list[Candidate]:
        out = []
        for i in range(len(batch["feasible"])):
            out.append(Candidate(
                times=times, s=batch["s"][i], d=batch["d"][i],
                s_dot=batch["s_dot"][i], xy=batch["xy"][i],
                heading=batch["heading"][i], curvature=batch["curvature"][i],
                speed=batch["speed"][i],
                target_d=float(batch["target_d"][i]),
                target_speed=float(batch["target_v"][i]),
                duration=float(batch["duration"][i]),
                cost=float(batch["cost"][i]), risk=float(batch["risk"][i]),
                feasible=bool(batch["feasible"][i]),
                reject_reason=batch["reason"][i]))
        return out
