"""Behaviour layer: an eight-state finite machine over the driving situation.

The lattice planner answers "which trajectory?"; this layer answers "what am I
trying to do?". Separating them matters because the same geometry means different
things: a stationary object 20 m ahead is a *follow* problem if it is a bus at a
stand and an *evade* problem if it is a rider coming the wrong way.

The state set is deliberately small and explicitly enumerated, because in B5 it is
re-expressed as a Stateflow chart and a chart is only readable if the states are.
Guards are evaluated in strict priority order, and a minimum dwell time stops the
machine chattering between neighbouring states on sensor noise.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np

from ..core.types import AgentClass
from ..prediction.intent import Manoeuvre, Prediction


class Behaviour(str, Enum):
    CRUISE = "cruise"                  # free road, hold desired speed
    FOLLOW = "follow"                  # matched to a leader's speed
    NUDGE = "nudge"                    # squeezing past an obstruction, reduced speed
    OVERTAKE = "overtake"              # actively passing something slower
    YIELD = "yield"                    # giving way at a junction or to a VRU
    CREEP = "creep"                    # dense traffic, walking pace, high alertness
    WRONG_WAY_EVADE = "wrong_way_evade"   # rider approaching head-on on our side
    EMERGENCY_STOP = "emergency_stop"  # stop now


@dataclass
class SceneSummary:
    """Everything the behaviour layer is allowed to reason about."""

    ego_speed: float
    speed_limit: float
    leader_gap: float = float("inf")
    leader_speed: float = float("inf")
    leader_class: AgentClass | None = None
    min_ttc: float = float("inf")
    wrong_way_gap: float = float("inf")
    vru_gap: float = float("inf")
    crossing_gap: float = float("inf")
    #: Free lateral space either side of the derived path, at its tightest.
    path_clearance: float = float("inf")
    #: Deviation from the nominal offset. Diagnostic only - not a guard input.
    reference_deviation: float = 0.0
    corridor_width: float = 8.0
    #: Agents per 100 m within the sensing horizon.
    density: float = 0.0
    lattice_infeasible: bool = False


@dataclass
class BehaviourDecision:
    state: Behaviour
    target_speed: float
    d_bias: float = 0.0
    reason: str = ""
    debug: dict = field(default_factory=dict)


@dataclass
class BehaviourConfig:
    desired_speed: float = 12.0
    #: Below this TTC the machine stops rather than negotiating.
    emergency_ttc: float = 1.6
    #: Head-on threat within this distance triggers evasion.
    wrong_way_range: float = 45.0
    #: A vulnerable road user is yielded to when we would reach it within this
    #: many seconds - not at a fixed distance. On an Indian road there is almost
    #: always a VRU within 15 m; yielding to mere proximity means never moving.
    vru_time_gap: float = 1.8
    #: Absolute floor: yield to anything vulnerable this close whatever our speed.
    vru_min_gap: float = 3.0
    #: Density above which the market-style creep behaviour engages.
    creep_density: float = 22.0
    #: Free lateral space below which we are "squeezing past something".
    nudge_clearance: float = 0.7
    #: Headway used to decide we are following rather than cruising.
    follow_headway: float = 2.4
    #: Speed ratio below which a leader is worth overtaking.
    overtake_ratio: float = 0.72
    #: Minimum time in a state before another may be entered, seconds.
    dwell: float = 0.6

    creep_speed: float = 2.2
    nudge_speed: float = 4.5
    yield_speed: float = 1.5
    evade_speed: float = 4.0


class BehaviourPlanner:
    """Priority-ordered guards with hysteresis."""

    def __init__(self, config: BehaviourConfig | None = None):
        self.cfg = config or BehaviourConfig()
        self.state = Behaviour.CRUISE
        self._entered_at = -1e9

    def decide(self, scene: SceneSummary, t: float) -> BehaviourDecision:
        cfg = self.cfg
        proposed, reason = self._guards(scene)

        # Hysteresis, except for the two states that must never be delayed.
        urgent = proposed in (Behaviour.EMERGENCY_STOP, Behaviour.WRONG_WAY_EVADE)
        if proposed is not self.state and not urgent and \
                (t - self._entered_at) < cfg.dwell:
            proposed, reason = self.state, "dwell"
        if proposed is not self.state:
            self._entered_at = t
        self.state = proposed

        target, bias = self._targets(proposed, scene)
        return BehaviourDecision(proposed, target, bias, reason,
                                 {"leader_gap": scene.leader_gap,
                                  "min_ttc": scene.min_ttc,
                                  "density": scene.density,
                                  "path_clearance": scene.path_clearance,
                                  "reference_deviation": scene.reference_deviation})

    def _guards(self, scene: SceneSummary) -> tuple[Behaviour, str]:
        cfg = self.cfg
        # An emergency stop is for a vehicle that is moving. Once stopped, with no
        # imminent contact, holding the state forever is itself the failure: the
        # target speed stays zero, every sampled trajectory is a stay-put
        # trajectory, and the vehicle can never recover. Fall through to CREEP,
        # which inches forward under the supervisor's protection.
        stopped = scene.ego_speed < 0.4
        if scene.min_ttc < cfg.emergency_ttc:
            return Behaviour.EMERGENCY_STOP, f"TTC {scene.min_ttc:.1f}s"
        if scene.lattice_infeasible:
            if stopped:
                return Behaviour.CREEP, "boxed in, inching"
            return Behaviour.EMERGENCY_STOP, "no feasible trajectory"
        if scene.wrong_way_gap < cfg.wrong_way_range:
            return Behaviour.WRONG_WAY_EVADE, \
                f"head-on agent at {scene.wrong_way_gap:.0f} m"
        # Yield to a vulnerable road user we are actually closing on, or to one
        # predicted to move into our path - not to every VRU merely present.
        #
        # The time-gap test only applies while genuinely moving. Evaluated with a
        # speed floor it becomes self-locking: a stopped vehicle computes a tiny
        # time gap to a VRU that is standing still beside it, yields, stays
        # stopped, and never recovers. Below walking pace only the absolute
        # proximity floor applies, and the risk field and safety supervisor carry
        # the rest.
        moving = scene.ego_speed > 1.0
        closing_on_vru = moving and (
            scene.vru_gap / scene.ego_speed < cfg.vru_time_gap
            or scene.crossing_gap / scene.ego_speed < cfg.vru_time_gap)
        if scene.vru_gap < cfg.vru_min_gap or closing_on_vru:
            return Behaviour.YIELD, "closing on a vulnerable road user"
        if scene.density > cfg.creep_density or scene.corridor_width < 4.2:
            return Behaviour.CREEP, "dense or constricted"
        if scene.path_clearance < cfg.nudge_clearance:
            return Behaviour.NUDGE, "squeezing past an obstruction"
        if np.isfinite(scene.leader_gap):
            headway = scene.leader_gap / max(scene.ego_speed, 0.5)
            slow = scene.leader_speed < cfg.overtake_ratio * scene.speed_limit
            if headway < cfg.follow_headway:
                # 5.5 m ruled out overtaking on exactly the roads where Indian
                # traffic overtakes constantly. A car plus a two-wheeler abreast
                # needs about 4 m, and that is the real threshold.
                if slow and scene.corridor_width > 4.2:
                    return Behaviour.OVERTAKE, "leader slow, space available"
                return Behaviour.FOLLOW, f"headway {headway:.1f}s"
        return Behaviour.CRUISE, "clear"

    def _targets(self, state: Behaviour,
                 scene: SceneSummary) -> tuple[float, float]:
        """Target speed and lateral bias for the lattice, per state."""
        cfg = self.cfg
        limit = min(cfg.desired_speed, scene.speed_limit)
        if state is Behaviour.EMERGENCY_STOP:
            return 0.0, 0.0
        if state is Behaviour.WRONG_WAY_EVADE:
            # Move away from the ego's own side, where the wrong-way rider is,
            # and slow enough that the manoeuvre is survivable if they mirror it.
            return min(cfg.evade_speed, limit), -0.9
        if state is Behaviour.YIELD:
            return min(cfg.yield_speed, limit), 0.0
        if state is Behaviour.CREEP:
            return min(cfg.creep_speed, limit), 0.0
        if state is Behaviour.NUDGE:
            return min(cfg.nudge_speed, limit), 0.0
        if state is Behaviour.OVERTAKE:
            return limit, -0.7          # India is left-hand traffic: pass on the right
        if state is Behaviour.FOLLOW:
            return float(np.clip(scene.leader_speed, 0.0, limit)), 0.0
        return limit, 0.0


def summarise_scene(ego_state, ego_frenet, tracks, predictions: list[Prediction],
                    corridor, reference, speed_limit: float,
                    ego_half_width: float) -> SceneSummary:
    """Reduce the perceived world to the handful of quantities the FSM uses.

    Keeping this a separate, explicit function matters: it is the *entire* input
    surface of the behaviour layer, so what the machine can and cannot react to is
    auditable in one place rather than scattered through the guards.
    """
    s_ego, d_ego, s_dot_ego, _ = ego_frenet
    summary = SceneSummary(ego_speed=max(s_dot_ego, 0.0), speed_limit=speed_limit,
                           path_clearance=reference.clearance,
                           reference_deviation=reference.deviation,
                           corridor_width=float(corridor.width_at(s_ego)))

    pred_by_id = {p.track_id: p for p in predictions}
    near = 0
    for tr in tracks:
        s, d = corridor.reference.to_frenet(tr.position)
        ds = s - s_ego
        if abs(ds) < 60.0:
            near += 1
        if ds <= 0.0:
            continue

        lateral_gap = abs(d - d_ego) - (ego_half_width + tr.width / 2.0)
        gap = ds - 2.1 - tr.length / 2.0

        # A leader is anything that would block us if we held our line.
        if lateral_gap < 0.6 and gap < summary.leader_gap:
            summary.leader_gap = float(max(gap, 0.0))
            summary.leader_speed = float(tr.speed)
            summary.leader_class = tr.cls

        pred = pred_by_id.get(tr.id)
        if pred is not None:
            best = max(pred.modes, key=lambda m: m.probability)
            if best.manoeuvre is Manoeuvre.WRONG_WAY and lateral_gap < 2.5:
                summary.wrong_way_gap = min(summary.wrong_way_gap, float(gap))
            if best.manoeuvre in (Manoeuvre.DART, Manoeuvre.CUT_IN,
                                  Manoeuvre.FILTER) and tr.cls.is_vru:
                summary.crossing_gap = min(summary.crossing_gap, float(gap))

        if tr.cls.is_vru and lateral_gap < 1.6:
            summary.vru_gap = min(summary.vru_gap, float(gap))

    summary.density = near / 1.2       # agents per 100 m over the 120 m window
    return summary
