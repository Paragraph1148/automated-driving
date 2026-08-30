"""The Indian Driving Risk Field (IDRF).

The central idea of SARATHI. A conventional planner asks a binary question of the
world - "is this cell occupied?" - and that question has no good answer on an Indian
road. A pothole is not occupied, but you should not take it at 14 m/s. A cow is not
occupied *yet*, but it might be anywhere within two metres in a second's time. A
two-wheeler beside you is occupied where it is, and also, probabilistically, in the
gap it is about to filter into.

So the IDRF replaces occupancy with a continuous, time-indexed cost field built
from four ingredients:

1. **Predicted occupancy**, summed over every manoeuvre hypothesis weighted by its
   probability - so the field *is* the expected cost, not the cost of the most
   likely future.
2. **Class-conditioned anisotropic kernels**, sized by each agent's footprint plus
   its predicted uncertainty. A bus is a tight, sharply bounded obstacle; a cow is
   a broad soft cloud.
3. **Harm weighting.** Striking a pedestrian is not equivalent to clipping a
   barricade, and the field says so explicitly rather than leaving it implicit in a
   safety-margin constant.
4. **Traversable terrain cost** - potholes and broken surface raise cost without
   ever becoming impassable, so the planner will cross a pothole slowly rather than
   swerve into oncoming traffic to avoid it.

The result is differentiable-ish, cheap to evaluate in bulk, and directly scoreable
by the local planner along a candidate trajectory.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..core.types import AgentClass
from ..prediction.intent import Prediction
from ..world.corridor import Corridor

#: Relative cost of striking each class. Deliberately harm-weighted rather than
#: ego-damage-weighted: an unprotected road user costs far more than sheet metal,
#: and on an Indian road most of the traffic is unprotected.
HARM_WEIGHT: dict[AgentClass, float] = {
    AgentClass.PEDESTRIAN: 1.00,
    AgentClass.BICYCLE: 0.95,
    AgentClass.TWO_WHEELER: 0.92,
    AgentClass.CATTLE: 0.80,
    AgentClass.PUSHCART: 0.78,
    AgentClass.STRAY_DOG: 0.62,
    AgentClass.AUTO_RICKSHAW: 0.70,
    AgentClass.CAR: 0.60,
    AgentClass.BUS: 0.66,
    AgentClass.TRUCK: 0.66,
    AgentClass.PARKED_VEHICLE: 0.50,
    AgentClass.BARRICADE: 0.40,
}


@dataclass
class RiskConfig:
    """Tuning for the field. Global - never tuned per scenario."""

    #: Clearance the ego wants around itself, added to every kernel.
    ego_margin: float = 0.45
    #: Weight on leaving the drivable corridor.
    corridor_weight: float = 3.0
    #: How far inside the corridor edge the boundary cost starts biting, metres.
    corridor_falloff: float = 1.2
    #: Weight on surface defects (potholes, broken surface).
    defect_weight: float = 0.9
    #: Cap on total risk at a point, so one pathological cell cannot dominate.
    risk_cap: float = 12.0


class IndianDrivingRiskField:
    """A time-indexed risk field built from multi-modal predictions."""

    def __init__(self, predictions: list[Prediction], corridor: Corridor,
                 times: np.ndarray, ego_length: float, ego_width: float,
                 config: RiskConfig | None = None):
        self.corridor = corridor
        self.cfg = config or RiskConfig()
        self.times = np.asarray(times, dtype=float)
        self.ego_half_l = ego_length / 2.0
        self.ego_half_w = ego_width / 2.0
        self._build(predictions)

    def _build(self, predictions: list[Prediction]) -> None:
        mu, heading, sl, sw, weight = [], [], [], [], []
        skirt_l, skirt_w, probability = [], [], []
        margin = self.cfg.ego_margin
        for pred in predictions:
            harm = HARM_WEIGHT.get(pred.cls, 0.6)
            half_l, half_w = pred.length / 2.0, pred.width / 2.0
            for mode in pred.modes:
                mu.append(mode.positions)
                heading.append(mode.headings)
                # Two separate quantities, deliberately not summed together.
                #
                # The *core* is the deterministic configuration-space footprint:
                # the agent's body, the ego's body, and a clearance margin. Inside
                # it the ego simply does not fit.
                #
                # The *skirt* is the predicted uncertainty, and it controls how
                # fast risk decays outside the core - not how big the core is.
                # Folding sigma into the core instead (the tempting shortcut)
                # inflates a bus into a 3.6 m half-width wall four seconds out and
                # closes roads that are in fact passable.
                core_l = half_l + self.ego_half_l + margin
                core_w = half_w + self.ego_half_w + margin
                sl.append(np.full_like(mode.sigma_long, core_l))
                sw.append(np.full_like(mode.sigma_lat, core_w))
                skirt_l.append(np.maximum(mode.sigma_long, 0.15))
                skirt_w.append(np.maximum(mode.sigma_lat, 0.15))
                weight.append(mode.probability * harm)
                probability.append(mode.probability)

        if mu:
            self.mu = np.stack(mu)                    # (K, T, 2)
            self.heading = np.stack(heading)          # (K, T)
            self.sl = np.stack(sl)                    # (K, T) core semi-length
            self.sw = np.stack(sw)                    # (K, T) core semi-width
            self.skirt_l = np.stack(skirt_l)          # (K, T) decay scale, long
            self.skirt_w = np.stack(skirt_w)          # (K, T) decay scale, lateral
            self.weight = np.asarray(weight)          # (K,)
            self._mode_probability = np.asarray(probability)
        else:
            n = len(self.times)
            self.mu = np.zeros((0, n, 2))
            self.heading = np.zeros((0, n))
            self.sl = np.ones((0, n))
            self.sw = np.ones((0, n))
            self.skirt_l = np.ones((0, n))
            self.skirt_w = np.ones((0, n))
            self.weight = np.zeros(0)
            self._mode_probability = np.zeros(0)

    # -- evaluation -------------------------------------------------------
    def _slice_at(self, t: float):
        """Linearly interpolate every kernel's parameters to time ``t``."""
        times = self.times
        if len(times) == 1:
            return (self.mu[:, 0], self.heading[:, 0], self.sl[:, 0],
                    self.sw[:, 0], self.skirt_l[:, 0], self.skirt_w[:, 0])
        i = int(np.clip(np.searchsorted(times, t) - 1, 0, len(times) - 2))
        span = times[i + 1] - times[i]
        a = 0.0 if span <= 0 else float(np.clip((t - times[i]) / span, 0.0, 1.0))
        lerp = lambda arr: (1 - a) * arr[:, i] + a * arr[:, i + 1]
        return (lerp(self.mu), lerp(self.heading), lerp(self.sl), lerp(self.sw),
                lerp(self.skirt_l), lerp(self.skirt_w))

    def agent_risk(self, points: np.ndarray, t: float) -> np.ndarray:
        """Expected collision cost at each of ``points`` (N,2) at time ``t``."""
        points = np.atleast_2d(np.asarray(points, dtype=float))
        if len(self.weight) == 0:
            return np.zeros(len(points))

        mu, heading, sl, sw, skirt_l, skirt_w = self._slice_at(t)
        delta = points[:, None, :] - mu[None, :, :]           # (N, K, 2)
        c, s = np.cos(heading), np.sin(heading)
        r_long = delta[..., 0] * c[None, :] + delta[..., 1] * s[None, :]
        r_lat = -delta[..., 0] * s[None, :] + delta[..., 1] * c[None, :]

        # Flat-topped anisotropic kernel: uniformly maximal across the whole
        # configuration-space core (so there is no spurious gradient pulling the
        # planner toward an obstacle's centre), then a Gaussian skirt whose width
        # is the prediction uncertainty, measured in metres beyond the core.
        excess_l = np.maximum(0.0, np.abs(r_long) - sl[None, :])
        excess_w = np.maximum(0.0, np.abs(r_lat) - sw[None, :])
        decay = np.exp(-0.5 * ((excess_l / skirt_l[None, :]) ** 2 +
                               (excess_w / skirt_w[None, :]) ** 2))
        return np.minimum(np.sum(self.weight[None, :] * decay, axis=1),
                          self.cfg.risk_cap)

    def penetration(self, points: np.ndarray, t: float,
                    min_probability: float = 0.05) -> np.ndarray:
        """Depth (m) by which each point intrudes into a predicted body.

        The soft risk field ranks trajectories; it must not be asked to *forbid*
        them. Harm weighting means a near-miss with a pedestrian legitimately
        scores higher than contact with a car - correct as a preference, useless as
        a constraint, because it would let the planner trade a collision for a
        wider berth.

        So collision is a hard constraint evaluated here, and anything with a
        positive return is infeasible regardless of what it costs. Only manoeuvre
        hypotheses above ``min_probability`` are treated as blocking; every tail
        hypothesis of every agent would otherwise close the road entirely.
        """
        points = np.atleast_2d(np.asarray(points, dtype=float))
        if len(self.weight) == 0:
            return np.zeros(len(points))

        mu, heading, sl, sw, _, _ = self._slice_at(t)
        active = self._mode_probability >= min_probability
        if not np.any(active):
            return np.zeros(len(points))
        mu, heading = mu[active], heading[active]
        sl, sw = sl[active], sw[active]

        delta = points[:, None, :] - mu[None, :, :]
        c, s = np.cos(heading), np.sin(heading)
        r_long = delta[..., 0] * c[None, :] + delta[..., 1] * s[None, :]
        r_lat = -delta[..., 0] * s[None, :] + delta[..., 1] * c[None, :]
        inside_l = sl[None, :] - np.abs(r_long)
        inside_w = sw[None, :] - np.abs(r_lat)
        depth = np.minimum(inside_l, inside_w)
        return np.maximum(0.0, depth.max(axis=1))

    def path_is_feasible(self, xy: np.ndarray, times: np.ndarray) -> bool:
        """True when no point of a space-time trajectory penetrates a body."""
        xy = np.atleast_2d(np.asarray(xy, dtype=float))
        times = np.asarray(times, dtype=float)
        for t_value in np.unique(times):
            idx = np.flatnonzero(times == t_value)
            if float(self.penetration(xy[idx], float(t_value)).max()) > 0.0:
                return False
        return True

    def terrain_risk(self, points: np.ndarray,
                     sd: tuple[np.ndarray, np.ndarray] | None = None
                     ) -> np.ndarray:
        """Static cost: corridor boundary plus surface defects.

        Pass ``sd`` (arrays of Frenet s and d) when the caller already has them -
        re-projecting every candidate trajectory point is otherwise the single
        most expensive thing the planner does.
        """
        points = np.atleast_2d(np.asarray(points, dtype=float))
        cfg = self.cfg
        risk = cfg.defect_weight * self.corridor.defect_cost(points)

        if sd is None:
            s, d = self.corridor.to_frenet_batch(points)
        else:
            s, d = np.asarray(sd[0], dtype=float), np.asarray(sd[1], dtype=float)

        d_min, d_max = self.corridor.bounds_at(s)
        # Distance from the point to the nearest edge, accounting for ego width.
        slack = np.minimum(d - (d_min + self.ego_half_w),
                           (d_max - self.ego_half_w) - d)
        # Ramps up as the ego approaches the verge, and keeps climbing beyond it,
        # so leaving the road is strongly discouraged but never numerically infinite.
        encroach = np.maximum(0.0, cfg.corridor_falloff - slack) / cfg.corridor_falloff
        risk = risk + cfg.corridor_weight * encroach ** 2
        risk = risk + cfg.corridor_weight * 2.0 * np.maximum(0.0, -slack)
        return np.minimum(risk, cfg.risk_cap)

    def evaluate(self, points: np.ndarray, t: float,
                 sd: tuple[np.ndarray, np.ndarray] | None = None) -> np.ndarray:
        """Total risk: predicted agents plus static terrain."""
        return np.minimum(self.agent_risk(points, t) + self.terrain_risk(points, sd),
                          self.cfg.risk_cap)

    def evaluate_path(self, xy: np.ndarray, times: np.ndarray,
                      sd: tuple[np.ndarray, np.ndarray] | None = None
                      ) -> np.ndarray:
        """Risk along a space-time trajectory - each point at *its own* time.

        This is the whole reason the field is time-indexed: a trajectory that
        crosses in front of a moving vehicle is safe or fatal depending entirely on
        *when* it gets there.
        """
        xy = np.atleast_2d(np.asarray(xy, dtype=float))
        times = np.asarray(times, dtype=float)
        out = np.empty(len(xy))
        # Group points sharing a timestep so each distinct time costs one batched
        # kernel evaluation rather than one per point.
        order = np.argsort(times, kind="stable")
        for t_value in np.unique(times):
            idx = order[times[order] == t_value]
            out[idx] = self.agent_risk(xy[idx], float(t_value))
        out += self.terrain_risk(xy, sd)
        return np.minimum(out, self.cfg.risk_cap)

    def grid(self, x_range: tuple[float, float], y_range: tuple[float, float],
             resolution: float, t: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Sample the field on a regular grid, for the Mission Control heat map."""
        xs = np.arange(x_range[0], x_range[1] + 1e-9, resolution)
        ys = np.arange(y_range[0], y_range[1] + 1e-9, resolution)
        gx, gy = np.meshgrid(xs, ys)
        pts = np.column_stack([gx.ravel(), gy.ravel()])
        values = self.evaluate(pts, t)
        return xs, ys, values.reshape(gx.shape)
