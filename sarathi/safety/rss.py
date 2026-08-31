"""Safety supervisor: RSS-India plus a control-barrier speed filter.

A sampling planner is only as safe as its cost weights, and cost weights are a
tuning artefact. This layer sits underneath it and is not negotiable: it computes,
from first principles, the fastest the ego may travel and still be able to stop or
yield without causing a collision, and it clips the planner's command to that.

It is **monotone by construction** - it can only ever reduce speed or increase
clearance, never the reverse. That property is what makes it worth having: no
tuning mistake upstream can make the vehicle more aggressive than this layer
permits.

The parameters are India-calibrated, and the calibration is the interesting part.
Textbook RSS assumes reaction times and lateral margins drawn from lane-disciplined
highway driving. Applied unmodified to a Delhi market street it demands gaps that
simply do not exist, the filter saturates, and the vehicle never moves. Real
behaviour here involves shorter reaction times, much smaller accepted lateral
gaps, and an explicit allowance for two-wheelers filtering past at close range.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..core.types import AgentClass, params_for


@dataclass
class RSSParams:
    """India-calibrated responsibility-sensitive safety parameters."""

    #: Response time before braking begins, seconds. Shorter than the 0.75-1.0 s
    #: used in highway RSS: drivers in dense mixed traffic are already anticipating.
    rho: float = 0.6
    #: Acceleration that may still occur during the response window, m/s^2.
    a_max_accel: float = 1.8
    #: Braking the ego guarantees it can achieve, m/s^2. Conservative on purpose.
    b_min: float = 3.6
    #: Braking the ego may use in an emergency, m/s^2.
    b_max: float = 6.5
    #: Lateral "fluid" margin, metres. Textbook RSS uses 0.5 m or more; on an
    #: Indian road two-wheelers routinely pass within 30 cm and demanding more
    #: makes the vehicle undriveable.
    mu_lateral: float = 0.25
    #: Extra lateral margin required around vulnerable road users.
    mu_vru_bonus: float = 0.35
    #: Control-barrier gain: how sharply the speed cap tightens as the margin
    #: closes. Higher is safer and jerkier.
    alpha: float = 1.5
    #: Never close within this bumper-to-bumper distance of a stopped vehicle
    #: in our own track, metres.
    #:
    #: RSS alone permits creeping to within centimetres, since at 0.09 m/s the
    #: stopping distance really is centimetres. That is safe and useless: a
    #: vehicle wedged 0.6 m behind a stopped bus cannot build the forward travel
    #: a lateral manoeuvre needs, so it can never pull out and waits for the bus
    #: instead. Human drivers leave room precisely so they can pull out; so do we.
    min_standoff: float = 2.0
    #: A leader below this speed counts as stopped for the standoff rule.
    standoff_leader_speed: float = 1.0


#: Braking we may assume a leading agent is capable of. Assuming a leader can
#: stop harder than it really can is the unsafe direction, so these are pessimistic
#: for heavy vehicles and generous only where physics allows.
LEADER_BRAKING: dict[AgentClass, float] = {
    AgentClass.TWO_WHEELER: 7.0,
    AgentClass.BICYCLE: 3.5,
    AgentClass.CAR: 7.5,
    AgentClass.AUTO_RICKSHAW: 6.0,
    AgentClass.BUS: 5.0,
    AgentClass.TRUCK: 4.5,
    AgentClass.PEDESTRIAN: 3.0,
    AgentClass.CATTLE: 2.5,
    AgentClass.STRAY_DOG: 5.0,
    AgentClass.PUSHCART: 1.8,
}


def safe_distance_same_direction(v_rear: float, v_front: float,
                                 p: RSSParams, b_front: float) -> float:
    """RSS minimum safe longitudinal distance when both travel the same way."""
    v_after = v_rear + p.rho * p.a_max_accel
    d = (v_rear * p.rho + 0.5 * p.a_max_accel * p.rho ** 2
         + v_after ** 2 / (2.0 * p.b_min)
         - v_front ** 2 / (2.0 * b_front))
    return max(d, 0.0)


def safe_distance_opposite(v_ego: float, v_other: float, p: RSSParams,
                           b_other: float) -> float:
    """RSS safe distance for head-on approach - the wrong-way rider case.

    Both vehicles may still accelerate through the response window, and neither
    can rely on the other braking, so the required distance grows with the *sum*
    of the speeds. This is why a wrong-way rider is so much more dangerous than a
    slow leader at the same range.
    """
    v_ego_after = v_ego + p.rho * p.a_max_accel
    v_oth_after = v_other + p.rho * p.a_max_accel
    d = ((v_ego + v_ego_after) / 2.0 * p.rho + v_ego_after ** 2 / (2.0 * p.b_min)
         + (v_other + v_oth_after) / 2.0 * p.rho
         + v_oth_after ** 2 / (2.0 * b_other))
    return max(d, 0.0)


def max_safe_speed_same_direction(gap: float, v_front: float, p: RSSParams,
                                  b_front: float) -> float:
    """Largest ego speed for which ``gap`` still satisfies RSS.

    Inverts :func:`safe_distance_same_direction`, which is quadratic in the ego
    speed. Solving rather than searching keeps the supervisor cheap enough to run
    on every agent every tick.
    """
    a = 1.0 / (2.0 * p.b_min)
    b = p.rho + p.rho * p.a_max_accel / p.b_min
    c = (0.5 * p.a_max_accel * p.rho ** 2
         + (p.rho * p.a_max_accel) ** 2 / (2.0 * p.b_min)
         - v_front ** 2 / (2.0 * b_front)
         - gap)
    disc = b * b - 4.0 * a * c
    if disc <= 0.0:
        return 0.0
    return max(0.0, (-b + math.sqrt(disc)) / (2.0 * a))


def max_safe_speed_opposite(gap: float, v_other: float, p: RSSParams,
                            b_other: float) -> float:
    """Largest ego speed for which a head-on ``gap`` still satisfies RSS."""
    v_oth_after = v_other + p.rho * p.a_max_accel
    budget = gap - ((v_other + v_oth_after) / 2.0 * p.rho
                    + v_oth_after ** 2 / (2.0 * b_other))
    if budget <= 0.0:
        return 0.0
    a = 1.0 / (2.0 * p.b_min)
    b = p.rho + p.rho * p.a_max_accel / p.b_min
    c = (0.5 * p.a_max_accel * p.rho ** 2
         + (p.rho * p.a_max_accel) ** 2 / (2.0 * p.b_min) - budget)
    disc = b * b - 4.0 * a * c
    if disc <= 0.0:
        return 0.0
    return max(0.0, (-b + math.sqrt(disc)) / (2.0 * a))


@dataclass
class SafetyVerdict:
    speed_cap: float
    accel_cap: float
    binding_track: int | None
    binding_reason: str
    intervened: bool


class SafetySupervisor:
    """Monotone speed and acceleration filter over the planner's command."""

    def __init__(self, params: RSSParams | None = None):
        self.p = params or RSSParams()
        self.interventions = 0

    def evaluate(self, ego_speed: float, ego_frenet, tracks, corridor,
                 ego_half_width: float, dt: float,
                 requested_accel: float) -> SafetyVerdict:
        p = self.p
        s_ego, d_ego, _, _ = ego_frenet
        cap = float("inf")
        binding: int | None = None
        reason = "clear"

        for tr in tracks:
            s, d = corridor.reference.to_frenet(tr.position)
            ds = s - s_ego
            if ds <= 0.0 or ds > 90.0:
                continue

            lateral_gap = abs(d - d_ego) - (ego_half_width + tr.width / 2.0)
            required_lateral = p.mu_lateral + (p.mu_vru_bonus if tr.cls.is_vru
                                               else 0.0)
            if lateral_gap > required_lateral:
                continue        # laterally clear, so longitudinally irrelevant

            gap = ds - 2.1 - tr.length / 2.0
            b_other = LEADER_BRAKING.get(tr.cls, 5.0)

            # Sign of the along-corridor velocity decides which rule applies.
            v_along = float(tr.velocity @ _tangent(corridor, s))
            if v_along < -0.5:
                limit = max_safe_speed_opposite(gap, abs(v_along), p, b_other)
                why = "head-on"
            else:
                limit = max_safe_speed_same_direction(gap, max(v_along, 0.0), p,
                                                      b_other)
                why = "leader"
                if v_along < p.standoff_leader_speed and gap < p.min_standoff:
                    limit = 0.0
                    why = "standoff"
            if limit < cap:
                cap, binding, reason = limit, tr.id, f"{why}:{tr.cls.value}"

        if not np.isfinite(cap):
            return SafetyVerdict(float("inf"), requested_accel, None, "clear",
                                 False)

        # Control-barrier form: rather than snapping to the cap, allow the speed
        # to approach it at a bounded rate. This keeps the filter smooth while
        # preserving the invariant, because the cap itself is never exceeded.
        margin = cap - ego_speed
        accel_cap = p.alpha * margin / max(dt, 1e-3) if margin < 0 else \
            p.alpha * margin + requested_accel
        accel_cap = float(np.clip(accel_cap, -p.b_max, p.b_max))

        allowed = min(requested_accel, accel_cap)
        intervened = allowed < requested_accel - 1e-6
        if intervened:
            self.interventions += 1
        return SafetyVerdict(cap, allowed, binding, reason, intervened)


def _tangent(corridor, s: float) -> np.ndarray:
    th = float(corridor.reference.heading_at(s))
    return np.array([math.cos(th), math.sin(th)])
