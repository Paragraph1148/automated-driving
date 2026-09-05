"""The SARATHI ego controller: the whole stack, composed.

Per tick:

    sense -> fuse -> predict -> risk field -> derive reference -> behaviour
          -> sample lattice -> track -> safety filter

Two things are deliberate. Perception runs on *simulated sensors*, never on ground
truth, so every downstream stage sees missed detections, class confusion and
occlusion. And the safety supervisor is the last stage, so nothing upstream - a
mis-weighted cost, a bad prediction - can produce a command it has not sanctioned.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..core.kinematics import max_steer_for, wheelbase_for
from ..core.types import AgentClass, params_for
from ..perception.fusion import Tracker
from ..perception.sensors import SensorSuite
from ..prediction.intent import IntentPredictor
from ..safety.rss import RSSParams, SafetySupervisor
from .base import ControlCommand, EgoController
from .behaviour import (Behaviour, BehaviourConfig, BehaviourPlanner,
                        summarise_scene)
from .corridor_path import (CorridorPathConfig, blockages_from_tracks,
                            derive_reference_path)
from .lattice import FrenetLatticePlanner, LatticeConfig
from .risk import IndianDrivingRiskField, RiskConfig


@dataclass
class SarathiConfig:
    desired_speed: float = 12.0
    #: Perception and prediction run at this divisor of the control rate. Sensing
    #: at 20 Hz buys nothing when tracks update on a 100 ms cadence anyway.
    perception_divisor: int = 2
    lookahead_time: float = 0.7
    #: Shortest pure-pursuit lookahead distance, metres.
    min_lookahead: float = 4.0
    #: Time constant of the speed controller. The original 0.4 s made it
    #: effectively bang-bang and was the dominant source of jerk.
    speed_tau: float = 0.9
    #: Limit on how fast the acceleration command itself may change, m/s^3.
    accel_rate_limit: float = 9.0
    #: How long the supervisor must report nowhere to go before the viewer is
    #: told, seconds. Long enough that a verdict flickering across three ticks
    #: never puts a banner on screen.
    blocked_hint_after: float = 1.5
    #: Ablation switches, used by the study in B6.
    use_risk_field: bool = True
    use_multimodal_prediction: bool = True
    use_safety_supervisor: bool = True
    use_derived_reference: bool = True


class SarathiController(EgoController):
    """Situation-Aware Risk-Adaptive Trajectory & Hazard Intelligence."""

    name = "sarathi"

    def __init__(self, config: SarathiConfig | None = None,
                 lattice: LatticeConfig | None = None,
                 behaviour: BehaviourConfig | None = None,
                 risk: RiskConfig | None = None,
                 rss: RSSParams | None = None,
                 corridor_path: CorridorPathConfig | None = None,
                 seed: int = 0):
        self.cfg = config or SarathiConfig()
        self.lattice = FrenetLatticePlanner(
            lattice, ego_half_width=params_for(AgentClass.CAR).width / 2.0)
        self.behaviour = BehaviourPlanner(
            behaviour or BehaviourConfig(desired_speed=self.cfg.desired_speed))
        self.risk_cfg = risk or RiskConfig()
        self.corridor_cfg = corridor_path or CorridorPathConfig()
        self.safety = SafetySupervisor(rss)
        self.predictor = IntentPredictor()
        self.params = params_for(AgentClass.CAR)
        self.seed = seed
        self._reset_state()

    def _reset_state(self) -> None:
        self.sensors: SensorSuite | None = None
        self.tracker: Tracker | None = None
        self.tracks: list = []
        self.predictions: list = []
        self.risk_field: IndianDrivingRiskField | None = None
        self.reference = None
        self.plan = None
        self.candidates: list = []
        self._tick = 0
        self._prev_d_ddot = 0.0
        self._prev_s_ddot = 0.0
        self._prev_accel = 0.0
        self._debug_cache: dict = {}
        #: When the supervisor first reported nowhere to go, or None.
        self._blocked_since: float | None = None

    def reset(self, scenario) -> None:
        self._reset_state()
        self.sensors = SensorSuite(seed=self.seed + scenario.seed,
                                   visibility=getattr(scenario, "visibility", 1.0))
        self.tracker = Tracker(scenario.dt * self.cfg.perception_divisor)
        self.behaviour.cfg.desired_speed = self.cfg.desired_speed

    # -- main entry point --------------------------------------------------
    def control(self, ego, view, corridor, t: float, dt: float) -> ControlCommand:
        cfg = self.cfg
        ego_frenet = view.frenet_of(ego.id)
        s_ego, d_ego, s_dot_ego, d_dot_ego = ego_frenet

        if self._tick % cfg.perception_divisor == 0:
            self._perceive(ego, view, corridor, d_ego)
        self._tick += 1

        speed_limit = corridor.comfortable_speed(ego.state.position,
                                                 cfg.desired_speed)

        # 1. Reference path from the drivable corridor, not from lane markings.
        blockages = blockages_from_tracks(
            self.tracks, corridor, ego_speed=speed_limit,
            cruise_speed=cfg.desired_speed) if cfg.use_derived_reference else []
        solution = derive_reference_path(
            corridor, s_ego, d_ego, blockages, self.params.width / 2.0,
            self.corridor_cfg)
        self.reference = solution.path

        # 2. Behaviour.
        scene = summarise_scene(ego.state, ego_frenet, self.tracks,
                                self.predictions, corridor, solution,
                                speed_limit, self.params.width / 2.0)
        scene.min_ttc = self._min_ttc(ego, corridor)
        decision = self.behaviour.decide(scene, t)

        # 3. Local trajectory, in the derived reference frame.
        rs, rd = self.reference.to_frenet(ego.state.position)
        th_err = self.reference.heading_error(ego.state)
        speed = max(ego.state.speed, 0.0)
        state = (rs, speed * math.cos(th_err), self._prev_s_ddot,
                 rd, speed * math.sin(th_err), self._prev_d_ddot)

        field = self.risk_field or IndianDrivingRiskField(
            [], corridor, self.predictor.times, self.params.length,
            self.params.width, self.risk_cfg)
        # Adaptive clearance: relax the comfort margin exactly where a human
        # driver would - creeping through a market, squeezing past a blockage.
        relief = {Behaviour.CREEP: 0.30, Behaviour.NUDGE: 0.22,
                  Behaviour.YIELD: 0.10}.get(decision.state, 0.0)
        self._margin_relief = relief
        # Lateral room available around the derived reference, expressed in that
        # reference's own frame, so the lattice only ever proposes in-road offsets.
        half_w = self.params.width / 2.0
        d_lo_c, d_hi_c = corridor.hard_bounds_at(s_ego)
        limits = (float(d_lo_c) + half_w - d_ego + rd,
                  float(d_hi_c) - half_w - d_ego + rd)
        best, candidates = self.lattice.plan(self.reference, state,
                                             decision.target_speed, field,
                                             d_bias=decision.d_bias,
                                             margin_relief=relief,
                                             lateral_limits=limits)
        self.candidates = candidates

        if best is None:
            # Nothing feasible: re-decide with the infeasibility flag set, which
            # forces EMERGENCY_STOP, and brake on the current heading.
            scene.lattice_infeasible = True
            decision = self.behaviour.decide(scene, t)
            self._margin_relief = relief
            self.plan = None
            accel, steer = -self.params.b_max, self._hold_steer(ego)
            self._prev_accel = accel
        else:
            self.plan = best
            accel, steer = self._track(ego, best, dt)
            # Carry a moderated version of the command as the next tick's initial
            # condition: enough for continuity, not so much that an emergency
            # decel poisons the next polynomial family.
            self._prev_s_ddot = float(np.clip(accel, -3.0, 2.0))
            self._prev_d_ddot = 0.0

        # 4. Safety supervisor has the last word, and can only slow us down.
        verdict = None
        if cfg.use_safety_supervisor:
            verdict = self.safety.evaluate(
                speed, ego_frenet, self.tracks, corridor,
                self.params.width / 2.0, dt, accel)
            accel = verdict.accel_cap

        # Asked every tick but answered cheaply: it returns at once unless the
        # vehicle has actually come to rest.
        blocked, blocker = self.safety.road_blocked(
            speed, ego_frenet, self.tracks, corridor, self.params.width / 2.0)
        self._debug_cache = self._debug(decision, best, verdict, solution, t,
                                        blocked, blocker)
        return ControlCommand(
            float(np.clip(accel, -self.params.b_max, self.params.a_max)),
            float(np.clip(steer, -max_steer_for(AgentClass.CAR),
                          max_steer_for(AgentClass.CAR))),
            self._debug_cache)

    # -- stages ------------------------------------------------------------
    def _perceive(self, ego, view, corridor, d_ego: float) -> None:
        others = [a for a in view.agents.values()
                  if a.id != ego.id and a.active]
        detections = self.sensors.sense(ego, others)
        self.tracks = self.tracker.update(detections)

        if self.cfg.use_multimodal_prediction:
            self.predictions = self.predictor.predict(self.tracks,
                                                      corridor.reference, d_ego)
        else:
            # Ablation: keep only the single most likely hypothesis per agent.
            self.predictions = []
            for pred in self.predictor.predict(self.tracks, corridor.reference,
                                               d_ego):
                best = max(pred.modes, key=lambda m: m.probability)
                best.probability = 1.0
                pred.modes = [best]
                self.predictions.append(pred)

        cfg = self.risk_cfg if self.cfg.use_risk_field else RiskConfig(
            ego_margin=self.risk_cfg.ego_margin, corridor_weight=0.0,
            defect_weight=0.0)
        self.risk_field = IndianDrivingRiskField(
            self.predictions, corridor, self.predictor.times,
            self.params.length, self.params.width, cfg)

    def _track(self, ego, plan, dt: float) -> tuple[float, float]:
        """Pure pursuit on the chosen trajectory, plus speed tracking.

        The lookahead point is chosen by *distance* along the plan, not by time.
        A stopped plan's samples all sit on top of the vehicle, so a time-indexed
        lookahead asks pure pursuit to steer toward a point zero metres away:
        atan2(0, 0) returns a meaningless bearing, the wheels go to full lock while
        stationary, and the moment the car creeps forward it leaves the road
        heading sideways. When the plan is too short, the reference path supplies
        the lookahead instead - it is always well defined.
        """
        lookahead = max(self.cfg.min_lookahead,
                        self.cfg.lookahead_time * max(ego.state.speed, 0.0))
        target = self._lookahead_point(ego, plan, lookahead)

        dx, dy = target[0] - ego.state.x, target[1] - ego.state.y
        ld = math.hypot(dx, dy)
        if ld < 0.5:
            # Even the reference gave us nothing usable; hold the road heading.
            return self._speed_command(plan, ego, dt), self._hold_steer(ego)
        alpha = math.atan2(dy, dx) - ego.state.heading
        alpha = (alpha + math.pi) % (2 * math.pi) - math.pi
        steer = math.atan2(2.0 * wheelbase_for(self.params) * math.sin(alpha),
                           max(ld, 1.0))

        return self._speed_command(plan, ego, dt), steer

    def _lookahead_point(self, ego, plan, lookahead: float) -> np.ndarray:
        """Point ``lookahead`` metres along the plan, or along the reference."""
        steps = np.linalg.norm(np.diff(plan.xy, axis=0), axis=1)
        travelled = np.concatenate([[0.0], np.cumsum(steps)])
        if travelled[-1] >= lookahead:
            i = int(np.searchsorted(travelled, lookahead))
            i = int(np.clip(i, 1, len(travelled) - 1))
            span = travelled[i] - travelled[i - 1]
            frac = 0.0 if span <= 1e-9 else (lookahead - travelled[i - 1]) / span
            return plan.xy[i - 1] + frac * (plan.xy[i] - plan.xy[i - 1])

        s_ego, _ = self.reference.to_frenet(ego.state.position)
        return np.asarray(self.reference.to_cartesian(
            min(s_ego + lookahead, self.reference.length), 0.0))

    def _speed_command(self, plan, ego, dt: float) -> float:
        idx = int(np.searchsorted(plan.times, self.cfg.lookahead_time))
        idx = int(np.clip(idx, 1, len(plan.times) - 1))
        v_target = float(plan.speed[idx])
        accel = (v_target - ego.state.speed) / self.cfg.speed_tau
        # Rate-limit the command so the vehicle does not chatter between full
        # throttle and full brake as the selected candidate changes.
        max_delta = self.cfg.accel_rate_limit * dt
        accel = float(np.clip(accel, self._prev_accel - max_delta,
                              self._prev_accel + max_delta))
        accel = float(np.clip(accel, -self.params.b_max, self.params.a_max))
        self._prev_accel = accel
        return accel

    def _hold_steer(self, ego) -> float:
        if self.reference is None:
            return 0.0
        return float(np.clip(-1.2 * self.reference.heading_error(ego.state),
                             -max_steer_for(AgentClass.CAR),
                             max_steer_for(AgentClass.CAR)))

    def _min_ttc(self, ego, corridor) -> float:
        """Minimum TTC over tracks, using the same multi-disc test as the metrics."""
        from ..metrics.run_metrics import _time_to_collision, disc_decomposition

        best = float("inf")
        ego_offsets, ego_r = disc_decomposition(self.params.length,
                                                self.params.width)
        axis = np.array([math.cos(ego.state.heading), math.sin(ego.state.heading)])
        ego_centres = ego.state.position[None, :] + ego_offsets[:, None] * axis
        for tr in self.tracks:
            offsets, r = disc_decomposition(tr.length, tr.width)
            tr_axis = np.array([math.cos(tr.heading), math.sin(tr.heading)])
            centres = tr.position[None, :] + offsets[:, None] * tr_axis
            rel_vel = tr.velocity - ego.state.velocity
            for c_e in ego_centres:
                for c_o in centres:
                    best = min(best, _time_to_collision(c_o - c_e, rel_vel,
                                                        ego_r + r))
        return best

    def _debug(self, decision, plan, verdict, solution, t: float,
               blocked: bool = False, blocker=None) -> dict:
        out = {
            "planner": self.name,
            "behaviour": decision.state.value,
            "behaviour_reason": decision.reason,
            "target_speed": round(decision.target_speed, 2),
            "path_clearance": round(float(solution.clearance), 2),
            "reference_deviation": round(float(solution.deviation), 2),
            "n_tracks": len(self.tracks),
            "n_candidates": len(self.candidates),
            "n_feasible": sum(1 for c in self.candidates if c.feasible),
            "margin_relief": round(float(getattr(self, "_margin_relief", 0.0)), 2),
        }
        if plan is not None:
            out["plan_cost"] = round(float(plan.cost), 3)
            out["plan_risk"] = round(float(plan.risk), 3)
            out["plan_target_d"] = round(float(plan.target_d), 2)
            out["plan_is_fallback"] = bool(plan.extras.get("safe_stop", False))
        if verdict is not None:
            out["safety_cap"] = (round(verdict.speed_cap, 2)
                                 if np.isfinite(verdict.speed_cap) else None)
            out["safety_binding"] = verdict.binding_reason
            out["safety_intervened"] = verdict.intervened
        # The supervisor has sampled the whole carriageway and found nowhere
        # clear: the one situation the vehicle cannot solve by itself. Held for
        # a moment before it is published, because a verdict that flickers
        # across three ticks is not something to put in front of a viewer.
        if blocked and self._blocked_since is None:
            self._blocked_since = t
        elif not blocked:
            self._blocked_since = None
        if self._blocked_since is not None and \
                t - self._blocked_since >= self.cfg.blocked_hint_after:
            out["blocked_for"] = round(t - self._blocked_since, 1)
            if blocker is not None:
                # Position and class, not the track id: the viewer holds
                # simulator agents, whose numbering is unrelated to the
                # tracker's.
                out["blocked_at"] = [round(float(blocker.x), 2),
                                     round(float(blocker.y), 2)]
                out["blocked_cls"] = blocker.cls.value
        return out
