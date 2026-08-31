"""Non-Lane-Based Intelligent Driver Model (NLB-IDM).

Classical IDM answers "how hard do I brake for the car *in my lane* ahead?". On an
Indian road there is no lane, and the vehicle that constrains you may be laterally
offset by 40 cm, straddling what a lane-based model would call a boundary, or
filtering diagonally through a gap that formally does not exist.

NLB-IDM keeps the IDM longitudinal core and adds the four extensions that the HDT
literature identifies as necessary:

1. **Dynamic pseudo-lane** - each agent claims a speed-dependent width rather than
   occupying a fixed lane.
2. **Centreline separation ratio** - a leader's influence decays with lateral
   overlap instead of switching on and off at a lane boundary.
3. **Minimum lateral clearance** - a class-specific lateral gap that generates a
   repulsive lateral acceleration.
4. **Vehicle-specific parameters** - a two-wheeler accepts gaps a bus never would.

Everything runs in corridor-Frenet coordinates ``(s, d)``, which is what makes
"lateral overlap" a well-posed quantity on a road with no markings.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..core.types import AgentClass, ClassParams

IDM_DELTA = 4.0          # IDM free-flow exponent

#: Lateral clearance, in metres, below which two bodies passing each other are
#: close enough that the following term must stay on. Wider than a scrape,
#: narrower than the 0.4 m a two-wheeler will happily pass a bus with.
SIDE_CLEARANCE = 0.25
MAX_INTERACTION = 12.0   # clamp on the IDM interaction term, keeps braking finite
#: Lateral agility below which a class simply does not filter through traffic.
#: Buses (0.5) and handcarts (0.4) sit below it; autos, cars and two-wheelers above.
GAP_SEEK_MIN_AGILITY = 0.6


@dataclass
class Neighbour:
    """One other road user, expressed in the shared corridor-Frenet frame."""

    id: int
    cls: AgentClass
    s: float
    d: float
    s_dot: float
    d_dot: float
    half_length: float
    half_width: float


def pseudo_lane_halfwidth(params: ClassParams, speed: float) -> float:
    """Half-width of the corridor an agent effectively claims at ``speed``.

    A stationary two-wheeler claims barely more than its handlebars; at 15 m/s the
    same rider wants most of a car's width. This speed dependence is what produces
    the observed "traffic self-organises into pseudo-lanes when it speeds up, and
    dissolves into a soup when it slows down" behaviour.
    """
    base = params.width / 2.0 + params.min_lateral_clearance
    return base + 0.08 * max(0.0, speed)


def separation_ratio(d_ego: float, half_w_ego: float,
                     d_other: float, half_w_other: float,
                     influence: float) -> float:
    """Lateral influence in [0,1] - the centreline separation ratio.

    1.0 when the two footprints overlap laterally, decaying smoothly to 0 once the
    clear lateral gap exceeds ``influence``. The smooth decay is the whole point:
    a lane-based model gives this a step discontinuity, and the step is what makes
    lane-based planners twitch when a two-wheeler hovers on a boundary.
    """
    gap = abs(d_ego - d_other) - (half_w_ego + half_w_other)
    if gap <= 0.0:
        return 1.0
    if gap >= influence:
        return 0.0
    u = 1.0 - gap / influence
    return float(u * u * (3.0 - 2.0 * u))     # smoothstep


def idm_interaction(speed: float, gap: float, delta_v: float,
                    params: ClassParams, aggression: float) -> float:
    """The IDM ``(s*/s)^2`` interaction term for a single leader.

    ``gap`` is bumper-to-bumper clearance; ``delta_v`` is closing speed (positive
    when approaching).
    """
    s0 = params.s0 / max(aggression, 0.4)
    T = params.T / max(aggression, 0.4)
    a = params.a_max * aggression
    b = params.b_comf
    s_star = s0 + max(0.0, speed * T + speed * delta_v / (2.0 * math.sqrt(a * b)))
    gap = max(gap, 0.15)
    return min((s_star / gap) ** 2, MAX_INTERACTION)


def longitudinal_accel(cls: AgentClass, params: ClassParams, aggression: float,
                       s: float, d: float, s_dot: float,
                       neighbours: list[Neighbour],
                       v_desired: float | None = None) -> tuple[float, int | None]:
    """NLB-IDM longitudinal acceleration.

    Returns ``(accel, governing_neighbour_id)``. The governing neighbour is exposed
    because the behaviour layer and the debug view both want to know *who* the ego
    is currently yielding to.
    """
    v0 = (v_desired if v_desired is not None else params.v_desired) * aggression
    v0 = max(v0, 0.5)
    speed = max(s_dot, 0.0)
    free = 1.0 - (speed / v0) ** IDM_DELTA

    half_w = params.width / 2.0
    influence = pseudo_lane_halfwidth(params, speed) * 2.0
    half_l = params.length / 2.0

    worst = 0.0
    worst_id: int | None = None
    for nb in neighbours:
        ds = nb.s - s
        if ds <= 0.0:
            continue                       # only agents ahead constrain us
        gap = ds - half_l - nb.half_length
        if gap > 90.0:
            continue
        w = separation_ratio(d, half_w, nb.d, nb.half_width, influence)
        if w <= 1e-3:
            continue

        # Being *alongside* something is not a following problem. When the two
        # footprints overlap longitudinally but are laterally clear, the geometric
        # gap goes negative, the IDM interaction term saturates, and the agent
        # brakes at maximum for ever - even while the other vehicle drives away.
        # In mixed traffic, where everything sits laterally offset from everything
        # else, this froze two-wheelers beside slower vehicles permanently.
        #
        # "Laterally clear" has to mean clear by a margin, though, not clear by a
        # millimetre. Skipping on any positive lateral gap let a vehicle close on
        # a stopped one, cross the point where the longitudinal gap goes negative,
        # stop braking, and scrape down its flank - which is how a stationary ego
        # was being struck at 4 m/s by traffic that had every chance to stop.
        lat_gap = abs(d - nb.d) - (half_w + nb.half_width)
        if gap <= 0.0 and lat_gap > SIDE_CLEARANCE:
            continue
        # Head-on traffic closes at the sum of speeds; that is exactly the
        # wrong-way case, and it must dominate the interaction term.
        delta_v = speed - nb.s_dot
        term = w * idm_interaction(speed, gap, delta_v, params, aggression)
        if term > worst:
            worst, worst_id = term, nb.id

    accel = params.a_max * aggression * (free - worst)
    return float(np.clip(accel, -params.b_max, params.a_max * aggression)), worst_id


def lateral_accel(cls: AgentClass, params: ClassParams, aggression: float,
                  s: float, d: float, s_dot: float, d_dot: float,
                  neighbours: list[Neighbour],
                  d_pref: float, d_min: float, d_max: float,
                  gap_seeking: float = 1.0) -> float:
    """Lateral acceleration: clearance repulsion + lane-free gap seeking.

    Four superposed terms:

    * **preference** - a soft pull toward ``d_pref`` (keep-left, or a policy target)
    * **damping** - so the agent settles instead of oscillating
    * **repulsion** - from neighbours violating this class's minimum lateral clearance
    * **gap seeking** - the behaviour that makes Indian traffic look the way it does:
      sample candidate lateral offsets and drift toward whichever has the most clear
      road ahead. Scaled by ``lateral_agility``, so two-wheelers filter and buses
      essentially do not.
    """
    k_pref = 0.35
    k_damp = 1.4
    accel = k_pref * (d_pref - d) - k_damp * d_dot

    half_w = params.width / 2.0
    want = params.min_lateral_clearance * (2.0 - min(aggression, 1.6))
    half_l = params.length / 2.0
    for nb in neighbours:
        ds = nb.s - s
        # Lateral repulsion is a *side-by-side* interaction. A vehicle 12 m ahead
        # in the same track is a following problem and belongs to the longitudinal
        # term; letting it push sideways here makes agents swerve at range.
        abreast = half_l + nb.half_length + 1.5
        if abs(ds) > abreast:
            continue
        lateral = d - nb.d
        clear = abs(lateral) - (half_w + nb.half_width)
        if clear >= want:
            continue
        overlap = min(1.0, (want - clear) / max(want, 0.1))
        urgency = 1.0 - abs(ds) / abreast
        direction = 1.0 if lateral >= 0.0 else -1.0
        if abs(lateral) < 1e-3:
            # Exactly overlapping. India is left-hand traffic, so the way past is
            # to the right - toward the road centre, i.e. decreasing d.
            direction = -1.0 if (d - d_min) > 0.5 else 1.0
        accel += 3.2 * overlap * urgency * direction

    if gap_seeking > 0.0 and params.lateral_agility >= GAP_SEEK_MIN_AGILITY \
            and s_dot > 0.5:
        accel += gap_seeking * _gap_seek(params, s, d, s_dot, neighbours,
                                         d_min, d_max)

    # Corridor edges are firm but not infinitely stiff - agents do clip verges.
    edge = 6.0
    if d > d_max - half_w:
        accel -= edge * (d - (d_max - half_w))
    if d < d_min + half_w:
        accel += edge * ((d_min + half_w) - d)

    limit = params.lateral_agility * 2.2
    return float(np.clip(accel, -limit, limit))


def _gap_seek(params: ClassParams, s: float, d: float, s_dot: float,
              neighbours: list[Neighbour], d_min: float, d_max: float) -> float:
    """Drift toward the lateral offset with the most clear road ahead."""
    half_w = params.width / 2.0
    lo, hi = d_min + half_w, d_max - half_w
    if hi - lo < 0.2:
        return 0.0
    candidates = np.linspace(lo, hi, 9)
    horizon = max(12.0, s_dot * 3.0)

    clear = np.full(len(candidates), horizon)
    for nb in neighbours:
        ds = nb.s - s - params.length / 2.0 - nb.half_length
        if ds <= 0.0 or ds > horizon:
            continue
        # Only traffic that would actually constrain us is worth moving around.
        # A leader travelling at our own speed is not an obstacle, and treating it
        # as one makes every agent weave permanently.
        if nb.s_dot >= s_dot - 0.5:
            continue
        blocked = np.abs(candidates - nb.d) < (half_w + nb.half_width +
                                               params.min_lateral_clearance)
        clear[blocked] = np.minimum(clear[blocked], ds)

    # Prefer more clear road, but penalise how far we would have to move to get it,
    # otherwise agents teleport across the road for a marginal gain.
    score = clear - 0.9 * np.abs(candidates - d)
    best = candidates[int(np.argmax(score))]
    if abs(best - d) < 0.15:
        return 0.0
    gain = 0.55 * params.lateral_agility
    return float(np.clip(gain * (best - d), -params.lateral_agility,
                         params.lateral_agility))
