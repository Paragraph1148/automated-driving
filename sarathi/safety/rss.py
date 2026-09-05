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
    #: Speed still permitted inside the standoff when a way past exists, m/s.
    #:
    #: The standoff used to clamp the cap to a hard zero here, which turned the
    #: rule into the exact failure it was written to prevent. A kinematic
    #: bicycle yaws at ``v tan(delta) / L``: at zero speed the steering has no
    #: authority whatsoever, so a vehicle forbidden to move can never turn out
    #: from behind the thing it is stopped behind. It waits instead - and a
    #: parked car, a barricade or a cow that has sat down never moves. Measured
    #: on village_road_unmarked before this: the ego stood still for 23-79% of
    #: every run, in stretches of up to 32 seconds, and never once reached its
    #: goal. Every single zero cap was this rule.
    #:
    #: A crawl restores the authority (1 m/s is ~15 deg/s of yaw) and gives up
    #: nothing: the RSS term is still evaluated on the true gap and still
    #: reaches zero on its own below about 0.45 m, so this can only ever govern
    #: inside the standoff band, never at the point of contact.
    standoff_crawl: float = 1.2


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


#: Half the ego's own body length, metres - subtracted from every measured
#: longitudinal gap so that gaps are bumper to bumper.
EGO_HALF_LENGTH = 2.1
#: Below this the vehicle counts as stopped for the blocked report, m/s.
BLOCKED_SPEED = 0.4
#: Clear road behind that is treated as "as much as we could ever want", metres.
REVERSE_ROOM_MAX = 30.0
#: Anything behind closing faster than this refuses the shunt outright, m/s.
#: Distance alone is not room: a car 10 m back doing 8 m/s leaves 5.8 m of
#: measured gap and covers it in under a second, and a vehicle reversing into
#: it is the one that caused the collision. Measured over 60 benchmark runs,
#: reversing on a static-distance check alone cost six collision-free runs.
REVERSE_CLOSING_SPEED = 1.0
#: How far back to look for traffic closing on us, metres.
REVERSE_CLOSING_RANGE = 30.0
#: Horizon over which "the road ahead is blocked" is judged, metres.
BLOCKED_LOOKAHEAD = 18.0

#: How far ahead an obstruction still blocks a lateral escape, metres.
#:
#: Deliberately short, and the shortness is the whole point. The test projects
#: everything in this window onto a single lateral axis, so anything inside it
#: is treated as though it were abreast of the vehicle. At 12 m that made a car
#: 12 m ahead and a handcart 5 m ahead - 7 m and several seconds apart - fill
#: the road between them and report no way through, on a carriageway with 1.7 m
#: of clear space beside the handcart. Six metres is about the distance covered
#: before the next decision at the crawl this gates, so what it collapses
#: together really is roughly abreast. Anything further off is still governed by
#: the RSS longitudinal term on the approach, and enters this window in time.
ESCAPE_LOOKAHEAD = 6.0


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
        #: Whether anywhere else on the carriageway is clear. Only the standoff
        #: rule needs it, so it is computed at most once and only when asked.
        escape: bool | None = None

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

            gap = ds - EGO_HALF_LENGTH - tr.length / 2.0
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
                    if escape is None:
                        escape = self._lateral_escape(s_ego, d_ego, tracks,
                                                      corridor, ego_half_width)
                    # Hold at a dead stop only when there is genuinely nowhere
                    # to go. Otherwise keep the crawl that gives the steering
                    # any authority at all - see ``standoff_crawl``.
                    limit = min(limit, p.standoff_crawl) if escape else 0.0
                    why = "standoff" if escape else "boxed-in"
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

    def reverse_room(self, ego_frenet, tracks, corridor,
                     ego_half_width: float) -> float:
        """Clear road behind the vehicle, bumper to bumper, in metres.

        The supervisor looks only forwards - ``evaluate`` skips everything with
        ``ds <= 0`` - because a vehicle that only ever drives forwards cannot
        hit what is behind it. A vehicle that may reverse can, so backing up
        needs its own answer, computed the same way and from the same tracks.

        The start of the corridor counts as an obstruction too: reversing off
        the end of the road is not an escape from anything.
        """
        s_ego, d_ego, _, _ = ego_frenet
        room = min(REVERSE_ROOM_MAX, max(0.0, float(s_ego)))
        for tr in tracks:
            s, d = corridor.reference.to_frenet(tr.position)
            ds = float(s) - float(s_ego)
            if ds >= 0.0:
                continue
            lateral = abs(float(d) - float(d_ego)) - (ego_half_width
                                                      + tr.width / 2.0)
            required = self.p.mu_lateral + (self.p.mu_vru_bonus
                                            if tr.cls.is_vru else 0.0)
            if lateral > required:
                continue               # laterally clear, so not behind us
            # Anything catching us up takes the whole manoeuvre off the table.
            # A gap is only room if it will still be there: backing into
            # approaching traffic makes the reversing vehicle the cause of the
            # collision, which is the one outcome this layer exists to prevent.
            v_along = float(tr.velocity @ _tangent(corridor, s))
            if -ds <= REVERSE_CLOSING_RANGE and v_along > REVERSE_CLOSING_SPEED:
                return 0.0
            gap = -ds - EGO_HALF_LENGTH - tr.length / 2.0
            room = min(room, max(0.0, gap))
        return float(room)

    def _lateral_escape(self, s_ego: float, d_ego: float, tracks, corridor,
                        ego_half_width: float,
                        lookahead: float = ESCAPE_LOOKAHEAD) -> bool:
        """Is there anywhere on this carriageway, beside where we are, to go?

        Sampled across the hard corridor bounds at the ego's own arc length: an
        offset counts as an escape if every track that could reach it is
        laterally clear of it by this class's required margin. Deliberately a
        question about the *road*, not about the plan - the supervisor must be
        able to answer it without trusting anything upstream, which is the whole
        reason it exists.
        """
        p = self.p
        d_lo, d_hi = corridor.hard_bounds_at(s_ego)
        lo, hi = float(d_lo) + ego_half_width, float(d_hi) - ego_half_width
        if hi <= lo:
            return False               # road narrower than the vehicle
        near = []
        for tr in tracks:
            s, d = corridor.reference.to_frenet(tr.position)
            ds = s - s_ego
            if -2.0 <= ds <= lookahead:
                need = p.mu_lateral + (p.mu_vru_bonus if tr.cls.is_vru else 0.0)
                near.append((float(d), tr.width / 2.0 + ego_half_width + need))
        for d_try in np.linspace(lo, hi, 13):
            if abs(d_try - d_ego) < 0.15:
                continue               # where we already are is not an escape
            if all(abs(d_try - d) >= reach for d, reach in near):
                return True
        return False


    def road_blocked(self, ego_speed: float, ego_frenet, tracks, corridor,
                     ego_half_width: float):
        # -> (blocked, nearest blocking track or None)
        """Is there no clear way across the carriageway ahead at all?

        Reported to whoever is watching, never used for control. It is the one
        situation the vehicle cannot resolve by itself and the one where a
        person watching has an option it does not: reaching in and moving
        whatever is in the way.

        Judged over a longer horizon than the crawl gate above, because this
        asks a question about the road rather than gating the next metre of
        travel - and only once the vehicle has actually come to rest, so a
        vehicle merely slowing for a leader is never reported as walled in.
        """
        if ego_speed > BLOCKED_SPEED:
            return False, None
        s_ego, d_ego, _, _ = ego_frenet
        if self._lateral_escape(s_ego, d_ego, tracks, corridor, ego_half_width,
                                BLOCKED_LOOKAHEAD):
            return False, None
        # Name the nearest thing in the way: that is the one a hand reaches for.
        # The *track* is returned rather than its id, because a track id belongs
        # to the tracker's own numbering and means nothing to a viewer holding
        # a list of simulator agents - matching one against the other lands the
        # marker on whichever unrelated vehicle happens to share the number.
        nearest, nearest_ds = None, float("inf")
        for tr in tracks:
            s, _ = corridor.reference.to_frenet(tr.position)
            ds = float(s) - float(s_ego)
            if 0.0 <= ds <= BLOCKED_LOOKAHEAD and ds < nearest_ds:
                nearest, nearest_ds = tr, ds
        return True, nearest


def _tangent(corridor, s: float) -> np.ndarray:
    th = float(corridor.reference.heading_at(s))
    return np.array([math.cos(th), math.sin(th)])
