"""The closed-loop simulator.

Fixed-step, fully seeded, and deterministic: the same scenario file plus the same
seed always produces the same run, which is what makes the Monte-Carlo campaign and
the ablation study meaningful rather than anecdotal.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

from ..agents.policies import PolicyContext, build_policy
from ..core.geom import convex_polygons_intersect
from ..core.kinematics import step_agent, step_bicycle, wheelbase_for
from ..core.types import Agent, AgentClass, State, params_for
from ..metrics.run_metrics import MetricsAccumulator, RunMetrics
from ..planning.base import ControlCommand, EgoController
from ..world.scenario import Scenario, populate
from .recorder import Recorder
from .view import FrenetView

EGO_ID = 0
#: How far outside the corridor edge the ego may stray before the run is failed.
OFF_ROAD_TOLERANCE = 0.6
#: Agents beyond this distance outside the corridor ends are removed.
DESPAWN_MARGIN = 30.0


@dataclass
class RunResult:
    metrics: RunMetrics
    recorder: Recorder | None
    wall_time: float


class Simulator:
    """Runs one scenario against one ego controller."""

    def __init__(self, scenario: Scenario, controller: EgoController,
                 record: bool = False, record_stride: int = 2,
                 seed: int | None = None, record_planner: bool = False):
        self.scenario = scenario
        self.controller = controller
        self.seed = scenario.seed if seed is None else seed
        self.rng = np.random.default_rng(self.seed)
        self.corridor = scenario.corridor
        self.dt = scenario.dt

        self.agents, policy_specs = populate(scenario, self.rng)
        self.policies = {aid: build_policy(name, **kwargs)
                         for aid, (name, kwargs) in policy_specs.items()}
        self._add_ego()

        self.recorder = Recorder(self.corridor, scenario.name,
                                 stride=record_stride, enabled=record)
        self.record_planner = record_planner and record
        self.metrics = MetricsAccumulator(scenario.name, self.seed,
                                          scenario.chaos, scenario.goal_s)
        self.t = 0.0
        self.finished = False
        self.outcome = ""
        self._collision_with: str | None = None
        self._impact_speed: float = 0.0
        controller.reset(scenario)

    # -- setup ------------------------------------------------------------
    def _add_ego(self) -> None:
        ref = self.corridor.reference
        spec = self.scenario.ego
        pos = ref.to_cartesian(spec["s"], spec["d"])
        ego = Agent(id=EGO_ID, cls=AgentClass.CAR,
                    state=State(float(pos[0]), float(pos[1]),
                                float(ref.heading_at(spec["s"])), spec["speed"]))
        self.agents[EGO_ID] = ego
        self.ego = ego

    # -- main loop --------------------------------------------------------
    def run(self, verbose: bool = False) -> RunResult:
        wall_start = time.perf_counter()
        max_steps = int(round(self.scenario.duration / self.dt))
        for _ in range(max_steps):
            self.step()
            if self.finished:
                break
        wall = time.perf_counter() - wall_start

        s_ego, *_ = self._ego_frenet_fallback()
        metrics = self.metrics.finalise(
            completed=self.outcome == "goal",
            collision=self.outcome == "collision",
            collision_with=self._collision_with,
            left_corridor=self.outcome == "off_road",
            ego_s=s_ego, impact_speed=self._impact_speed)
        if verbose:
            print(metrics.summary())
        return RunResult(metrics, self.recorder if self.recorder.enabled else None,
                         wall)

    def step(self) -> None:
        view = FrenetView(self.corridor, self.agents)

        # 1. Ego decision, timed. This is the PS's "replanning latency".
        t0 = time.perf_counter()
        cmd = self.controller.control(self.ego, view, self.corridor,
                                      self.t, self.dt)
        latency_ms = (time.perf_counter() - t0) * 1000.0

        # 2. Every other road user acts. Traffic is oblivious to being simulated;
        #    it reacts to the ego only through ordinary NLB-IDM interactions.
        commands: dict[int, tuple[float, float]] = {}
        for aid, agent in self.agents.items():
            if aid == EGO_ID or not agent.active:
                continue
            commands[aid] = self._agent_command(agent, view)

        # 3. Integrate. Ego first so its recorded state matches the command above.
        self._integrate_ego(cmd)
        for aid, (accel, lateral) in commands.items():
            agent = self.agents[aid]
            agent.state = step_agent(agent.cls, agent.params, agent.state,
                                     accel, lateral, self.dt)

        self.t += self.dt
        self._despawn()

        others = [a for aid, a in self.agents.items()
                  if aid != EGO_ID and a.active]
        self.metrics.tick(self.dt, self.ego, others, cmd.steer, latency_ms)
        layers = None
        if self.record_planner:
            from .snapshot import planner_snapshot
            layers = planner_snapshot(self.controller, self.ego)
        self.recorder.capture(self.t, self.agents, EGO_ID, cmd.debug, layers)
        self._check_termination(view, others)

    def _agent_command(self, agent: Agent, view: FrenetView) -> tuple[float, float]:
        s, d, s_dot, d_dot = view.frenet_of(agent.id)
        d_min, d_max = self.corridor.bounds_at(s)
        ctx = PolicyContext(
            s=s, d=d, s_dot=s_dot, d_dot=d_dot,
            neighbours=view.neighbours_of(agent.id),
            d_min=float(d_min), d_max=float(d_max),
            d_nominal=float(self.corridor.nominal_offset(s)),
            corridor_length=self.corridor.reference.length,
            speed_limit=self.corridor.comfortable_speed(agent.state.position,
                                                        agent.params.v_desired),
            dt=self.dt, time=self.t, ego=self.ego)
        return self.policies[agent.id].act(agent, ctx, self.rng)

    def _integrate_ego(self, cmd: ControlCommand) -> None:
        p = self.ego.params
        self.ego.state = step_bicycle(
            self.ego.state, cmd.accel, cmd.steer, self.dt,
            wheelbase_for(p), v_max=p.v_desired * 1.6, v_min=0.0)

    def _despawn(self) -> None:
        length = self.corridor.reference.length
        ref = self.corridor.reference
        for aid, agent in self.agents.items():
            if aid == EGO_ID or not agent.active:
                continue
            s, _ = ref.to_frenet(agent.state.position)
            # Agents pinned at either end of the corridor have driven off it.
            if s <= 0.05 and agent.state.speed > 0.1:
                agent.active = False
            elif s >= length - 0.05 and agent.state.speed > 0.1:
                agent.active = False

    def _check_termination(self, view: FrenetView, others: list[Agent]) -> None:
        ref = self.corridor.reference
        s, d = ref.to_frenet(self.ego.state.position)

        ego_poly = self.ego.corners()
        ego_r = self.ego.footprint.radius()
        ego_p = self.ego.state.position
        for other in others:
            if float(np.linalg.norm(other.state.position - ego_p)) > \
                    ego_r + other.footprint.radius():
                continue
            if convex_polygons_intersect(ego_poly, other.corners()):
                self.outcome = "collision"
                self._collision_with = other.cls.value
                self._impact_speed = float(np.linalg.norm(
                    other.state.velocity - self.ego.state.velocity))
                self.finished = True
                return

        d_min, d_max = self.corridor.bounds_at(s)
        half_w = self.ego.params.width / 2.0
        if d > float(d_max) - half_w + OFF_ROAD_TOLERANCE or \
                d < float(d_min) + half_w - OFF_ROAD_TOLERANCE:
            self.outcome = "off_road"
            self.finished = True
            return

        if s >= self.scenario.goal_s:
            self.outcome = "goal"
            self.finished = True

    def _ego_frenet_fallback(self) -> tuple[float, float]:
        return self.corridor.reference.to_frenet(self.ego.state.position)
