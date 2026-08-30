"""Multi-modal intent and trajectory prediction.

A single predicted trajectory per agent is the wrong model for an Indian road. A
two-wheeler beside you is *either* holding station *or* about to filter into the
40 cm gap ahead of you, and those two futures are metres apart. Committing to the
mean of them produces a prediction that is confidently wrong in a way that is worse
than no prediction at all.

So every track gets a distribution over manoeuvres, each manoeuvre gets its own
trajectory, and each trajectory carries a covariance that grows with time and
shrinks with how predictable that class is. A bus gets a tight, near-deterministic
tube. A cow gets a wide, nearly isotropic cloud - which is the honest answer.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np

from ..core.frenet import ReferencePath
from ..core.kinematics import HOLONOMIC_CLASSES
from ..core.types import AgentClass, params_for
from ..perception.fusion import Track

#: Lateral displacement a wheeled vehicle can achieve per metre travelled forward.
#: Roughly tan(25 deg) - a brisk but achievable lane change.
LATERAL_SLIP_RATIO = 0.45


class Manoeuvre(str, Enum):
    CONTINUE = "continue"      # hold current speed and lateral position
    CUT_IN = "cut_in"          # move laterally into the ego's path
    FILTER = "filter"          # thread through a gap (two-wheelers, autos)
    STOP = "stop"              # decelerate to standstill
    DART = "dart"              # sudden large lateral movement (VRUs, animals)
    WRONG_WAY = "wrong_way"    # travelling against the corridor direction


#: Prior probability of each manoeuvre, conditioned on class. These encode the
#: behaviours the problem statement calls out: two-wheelers filter, animals and
#: pedestrians dart, heavy vehicles essentially do not deviate.
MANOEUVRE_PRIORS: dict[AgentClass, dict[Manoeuvre, float]] = {
    AgentClass.CAR: {Manoeuvre.CONTINUE: 0.74, Manoeuvre.CUT_IN: 0.14,
                     Manoeuvre.STOP: 0.10, Manoeuvre.DART: 0.02},
    AgentClass.BUS: {Manoeuvre.CONTINUE: 0.84, Manoeuvre.STOP: 0.14,
                     Manoeuvre.CUT_IN: 0.02},
    AgentClass.TRUCK: {Manoeuvre.CONTINUE: 0.86, Manoeuvre.STOP: 0.12,
                       Manoeuvre.CUT_IN: 0.02},
    AgentClass.AUTO_RICKSHAW: {Manoeuvre.CONTINUE: 0.52, Manoeuvre.FILTER: 0.18,
                               Manoeuvre.CUT_IN: 0.14, Manoeuvre.STOP: 0.16},
    AgentClass.TWO_WHEELER: {Manoeuvre.CONTINUE: 0.46, Manoeuvre.FILTER: 0.26,
                             Manoeuvre.CUT_IN: 0.20, Manoeuvre.STOP: 0.05,
                             Manoeuvre.DART: 0.03},
    AgentClass.BICYCLE: {Manoeuvre.CONTINUE: 0.58, Manoeuvre.FILTER: 0.14,
                         Manoeuvre.CUT_IN: 0.16, Manoeuvre.STOP: 0.08,
                         Manoeuvre.DART: 0.04},
    AgentClass.PEDESTRIAN: {Manoeuvre.CONTINUE: 0.30, Manoeuvre.STOP: 0.34,
                            Manoeuvre.DART: 0.36},
    AgentClass.CATTLE: {Manoeuvre.CONTINUE: 0.26, Manoeuvre.STOP: 0.38,
                        Manoeuvre.DART: 0.36},
    AgentClass.STRAY_DOG: {Manoeuvre.CONTINUE: 0.22, Manoeuvre.STOP: 0.24,
                           Manoeuvre.DART: 0.54},
    AgentClass.PUSHCART: {Manoeuvre.CONTINUE: 0.66, Manoeuvre.STOP: 0.30,
                          Manoeuvre.CUT_IN: 0.04},
}
STATIC_PRIOR = {Manoeuvre.CONTINUE: 1.0}


@dataclass
class PredictedMode:
    """One hypothesised future for one agent."""

    manoeuvre: Manoeuvre
    probability: float
    positions: np.ndarray     # (T, 2) world-frame
    headings: np.ndarray      # (T,)
    sigma_long: np.ndarray    # (T,) along-heading standard deviation
    sigma_lat: np.ndarray     # (T,) across-heading standard deviation


@dataclass
class Prediction:
    track_id: int
    cls: AgentClass
    cls_confidence: float
    length: float
    width: float
    modes: list[PredictedMode]


class IntentPredictor:
    """Turns fused tracks into multi-modal predictions in corridor-Frenet space."""

    def __init__(self, horizon: float = 4.0, step: float = 0.4,
                 lateral_shift: float = 2.0):
        self.times = np.arange(0.0, horizon + 1e-9, step)
        self.lateral_shift = lateral_shift
        self.holonomic = None      # set per-agent during roll-out

    def predict(self, tracks: list[Track], reference: ReferencePath,
                ego_d: float = 0.0) -> list[Prediction]:
        return [self._predict_one(tr, reference, ego_d) for tr in tracks]

    def _predict_one(self, tr: Track, ref: ReferencePath,
                     ego_d: float) -> Prediction:
        params = params_for(tr.cls)
        s, d = ref.to_frenet(tr.position)
        th_ref = float(ref.heading_at(s))
        v = tr.velocity
        s_dot = float(v[0] * np.cos(th_ref) + v[1] * np.sin(th_ref))
        d_dot = float(-v[0] * np.sin(th_ref) + v[1] * np.cos(th_ref))

        priors = self._priors(tr, s_dot)
        modes = []
        for manoeuvre, prob in priors.items():
            if prob < 0.02:
                continue
            self.holonomic = lambda _p, c=tr.cls: c in HOLONOMIC_CLASSES
            s_traj, d_traj = self._roll_out(manoeuvre, s, d, s_dot, d_dot,
                                            params, ego_d)
            xy, headings = ref.frenet_traj_to_cartesian(s_traj, d_traj)
            sl, sw = self._uncertainty(tr, params, manoeuvre)
            modes.append(PredictedMode(manoeuvre, prob, xy, headings, sl, sw))
        return Prediction(tr.id, tr.cls, tr.cls_confidence,
                          params.length, params.width, modes)

    def _priors(self, tr: Track, s_dot: float) -> dict[Manoeuvre, float]:
        """Class priors, then reshaped by what the track is observably doing."""
        if tr.cls.is_static:
            return dict(STATIC_PRIOR)
        priors = dict(MANOEUVRE_PRIORS.get(tr.cls, MANOEUVRE_PRIORS[AgentClass.CAR]))

        # An agent measurably travelling against the corridor is not "maybe" going
        # the wrong way - it already is, and the prior must collapse onto that.
        if s_dot < -1.0:
            priors = {Manoeuvre.WRONG_WAY: 0.86, Manoeuvre.CONTINUE: 0.08,
                      Manoeuvre.STOP: 0.06}
        elif abs(s_dot) < 0.4:
            priors[Manoeuvre.STOP] = priors.get(Manoeuvre.STOP, 0.1) + 0.45

        # A class we are unsure about deserves a hedged, more spread-out set of
        # hypotheses - low classifier confidence should widen the distribution,
        # not silently pick the argmax and plan against it.
        if tr.cls_confidence < 0.7:
            for m in (Manoeuvre.DART, Manoeuvre.STOP):
                priors[m] = priors.get(m, 0.0) + 0.12

        total = sum(priors.values())
        return {m: p / total for m, p in priors.items()}

    def _roll_out(self, manoeuvre: Manoeuvre, s: float, d: float, s_dot: float,
                  d_dot: float, params, ego_d: float
                  ) -> tuple[np.ndarray, np.ndarray]:
        t = self.times
        if manoeuvre is Manoeuvre.STOP:
            decel = params.b_comf
            t_stop = max(abs(s_dot) / max(decel, 1e-3), 1e-3)
            tc = np.minimum(t, t_stop)
            s_traj = s + np.sign(s_dot) * (abs(s_dot) * tc - 0.5 * decel * tc ** 2)
            return s_traj, np.full_like(t, d)

        s_traj = s + s_dot * t
        if manoeuvre in (Manoeuvre.CONTINUE, Manoeuvre.WRONG_WAY):
            return s_traj, d + d_dot * np.minimum(t, 1.0)

        # Lateral manoeuvres use a smoothstep so the predicted path is C1 - a
        # kinked prediction produces a kinked risk field and a twitchy planner.
        duration = {Manoeuvre.CUT_IN: 2.5, Manoeuvre.FILTER: 1.8,
                    Manoeuvre.DART: 1.0}[manoeuvre]
        u = np.clip(t / duration, 0.0, 1.0)
        shape = u * u * (3.0 - 2.0 * u)

        if manoeuvre is Manoeuvre.CUT_IN:
            target = ego_d
        elif manoeuvre is Manoeuvre.FILTER:
            target = d + np.sign(ego_d - d or 1.0) * self.lateral_shift
        else:                                    # DART
            target = d + np.sign(ego_d - d or 1.0) * (self.lateral_shift * 1.4)

        # A wheeled vehicle cannot translate sideways: its lateral displacement is
        # bounded by how far it travels forward. Without this a *parked* car is
        # predicted to cut in by 1.8 m, and every stationary vehicle on the road
        # becomes a swerving hazard that closes the lane beside it.
        shift = target - d
        if self.holonomic and self.holonomic(params):
            max_shift = params.lateral_agility * duration
        else:
            max_shift = LATERAL_SLIP_RATIO * abs(s_dot) * duration
        shift = float(np.clip(shift, -max_shift, max_shift))
        return s_traj, d + shift * shape

    def _uncertainty(self, tr: Track, params,
                     manoeuvre: Manoeuvre) -> tuple[np.ndarray, np.ndarray]:
        """Anisotropic, time-growing uncertainty.

        Longitudinal spread comes from speed uncertainty; lateral spread comes from
        the class's observed tendency to wander, inflated when we are unsure what
        the class even is.
        """
        t = self.times
        base = tr.position_sigma
        unpredictability = 2.0 - params.predictability
        hedge = 1.0 + 0.8 * (1.0 - tr.cls_confidence)

        sigma_long = base + 0.45 * unpredictability * t + 0.06 * tr.speed * t
        sigma_lat = (base + params.lateral_sigma_rate * unpredictability * t
                     * hedge)
        if manoeuvre is Manoeuvre.DART:
            sigma_lat = sigma_lat * 1.6
        elif manoeuvre is Manoeuvre.STOP:
            sigma_long = sigma_long * 1.3

        # Clamp to what the class can physically reach. Without this an animal
        # with a high unpredictability score accumulates a lateral sigma of nine
        # metres over a four-second horizon - wider than the road - and the planner
        # correctly concludes there is nowhere safe to go. Uncertainty must stay
        # inside the reachable set: 2 sigma covers it.
        reach_lat = np.maximum(0.3, params.lateral_agility * t / 2.0)
        reach_long = np.maximum(0.3,
                                (params.v_desired * 1.6 + tr.speed) * t / 2.0)
        return (np.minimum(sigma_long, reach_long),
                np.minimum(sigma_lat, reach_lat))
