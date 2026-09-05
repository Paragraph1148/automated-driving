"""The closed-loop simulator.

Fixed-step, fully seeded, and deterministic: the same scenario file plus the same
seed always produces the same run, which is what makes the Monte-Carlo campaign and
the ablation study meaningful rather than anecdotal.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass

import numpy as np

from ..agents.policies import PolicyContext, build_policy
from ..core.geom import convex_polygons_intersect
from ..core.kinematics import step_agent, step_bicycle, wheelbase_for
from ..core.types import Agent, AgentClass, State, params_for, wrap_to_pi
from ..metrics.run_metrics import MetricsAccumulator, RunMetrics
from ..planning.base import ControlCommand, EgoController
from ..world.scenario import Scenario, populate
from .recorder import Recorder
from .view import FrenetView

EGO_ID = 0
#: How far beyond the verge the ego may stray before the run is failed.
OFF_ROAD_TOLERANCE = 0.25
#: Agents beyond this distance outside the corridor ends are removed.
DESPAWN_MARGIN = 30.0
#: Fastest the ego may travel backwards, m/s. Walking pace: a shunt is a
#: last-resort manoeuvre in a place with no room, never a way of making progress.
EGO_REVERSE_LIMIT = 1.4
#: How far inside the corridor a re-entering road user appears, metres. Far
#: enough in that it is visible arriving rather than materialising on the edge
#: of the frame, near enough out that it does not appear in front of the ego.
FLOW_ENTRY_INSET = 4.0
#: Clear road a re-entering road user needs, metres, on top of both bodies'
#: half-lengths. Traffic waits its turn at the entry rather than spawning on
#: top of whatever is already sitting there.
FLOW_ENTRY_GAP = 12.0
#: Longest queue of road users waiting to re-enter. A cap, not a target: if the
#: entry stays blocked this long the road is saturated anyway, and an unbounded
#: queue would dump the whole backlog the instant it cleared.
FLOW_QUEUE_MAX = 24
#: Retired agents tolerated before they are swept out of the world dict. A live
#: session runs for as long as somebody is watching, and every despawn used to
#: leave a corpse behind that each subsequent tick still had to iterate over.
REAP_THRESHOLD = 32
#: How far a wheeled road user's heading may deviate from its direction of travel
#: along the road, radians. Generous enough for lane changes and filtering,
#: tight enough that a U-turn is impossible.
HEADING_CONE = math.radians(70.0)


@dataclass
class RunResult:
    metrics: RunMetrics
    recorder: Recorder | None
    wall_time: float


class Simulator:
    """Runs one scenario against one ego controller."""

    def __init__(self, scenario: Scenario, controller: EgoController,
                 record: bool = False, record_stride: int = 2,
                 seed: int | None = None, record_planner: bool = False,
                 live: bool = False):
        self.scenario = scenario
        self.controller = controller
        self.seed = scenario.seed if seed is None else seed
        self.rng = np.random.default_rng(self.seed)
        self.corridor = scenario.corridor
        self.dt = scenario.dt

        self.agents, policy_specs = populate(scenario, self.rng)
        self.policies = {aid: build_policy(name, **kwargs)
                         for aid, (name, kwargs) in policy_specs.items()}
        #: How each agent was built, kept so a road user that drives off the far
        #: end can be sent back in at the near one as the same kind of traffic.
        self._policy_specs: dict[int, tuple[str, dict]] = dict(policy_specs)
        #: Monotonic, never reused. Ids are handed to the viewer, which sends
        #: them back to grab and drag; reissuing one that has just been swept up
        #: would put somebody's hand on a different vehicle than the one they
        #: reached for.
        self._next_id = max(self.agents, default=0) + 1
        self._add_ego()

        self.recorder = Recorder(self.corridor, scenario.name,
                                 stride=record_stride, enabled=record)
        self.record_planner = record_planner and record
        self.metrics = MetricsAccumulator(scenario.name, self.seed,
                                          scenario.chaos, scenario.goal_s)
        self.t = 0.0
        #: In a live session the run never ends: contact and off-road are recorded
        #: as events and the world keeps running. A demo that freezes the moment
        #: something goes wrong is a demo nobody can explore.
        self.live = live
        self.events: list[dict] = []
        #: Agents currently held by the operator's mouse. They are exempt from
        #: their policy and from integration, so the world keeps running around
        #: a vehicle being positioned by hand.
        self.held: set[int] = set()
        #: World points where an object stopped existing since the last tick -
        #: erased by the operator, or cleared after contact. Handed to the
        #: controller so its tracker does not coast a vehicle that is no longer
        #: anywhere; no sensor can tell that from an occlusion.
        self.vanished: list[tuple[float, float]] = []
        #: Road users that have left the corridor and are waiting for a gap to
        #: re-enter at the other end. See :meth:`_admit_entries`.
        self._pending_entry: list[dict] = []
        self.finished = False
        self.outcome = ""
        self._collision_with: str | None = None
        self._ego_speed_at_impact = 0.0
        self.last_replan_ms = 0.0
        self._impact_bearing = 0.0
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

    # -- live editing -----------------------------------------------------
    def spawn(self, cls: AgentClass, x: float, y: float,
              heading: float | None = None, speed: float | None = None,
              policy: str | None = None, aggression: float = 1.0) -> int:
        """Add a road user mid-run, at a world position.

        This is what makes the demo defensible rather than decorative: a judge
        drops a two-wheeler in front of the vehicle and watches the same planner,
        with no foreknowledge of it, perceive and react to it.
        """
        ref = self.corridor.reference
        s, _ = ref.to_frenet(np.array([x, y], dtype=float))
        if heading is None:
            heading = float(ref.heading_at(s))
        if policy is None:
            policy = "static" if cls.is_static else "traffic"
        if speed is None:
            speed = 0.0 if cls.is_static else params_for(cls).v_desired * 0.6
        if policy in ("oncoming", "wrong_way"):
            heading = float(heading + math.pi)

        agent_id = self._next_agent_id()
        agent = Agent(id=agent_id, cls=cls,
                      state=State(float(x), float(y), float(heading),
                                  float(max(speed, 0.0))),
                      aggression=aggression)
        self.agents[agent_id] = agent
        self.policies[agent_id] = build_policy(policy)
        self._policy_specs[agent_id] = (policy, {})
        return agent_id

    def agent_near(self, x: float, y: float,
                   radius: float = 4.0) -> int | None:
        """Id of the road user nearest a point, ego included. None if nothing close."""
        point = np.array([x, y], dtype=float)
        best, best_dist = None, radius
        for aid, agent in self.agents.items():
            if not agent.active:
                continue
            # Measure to the body, so a bus is grabbable anywhere along its length.
            reach = max(agent.params.length, agent.params.width) / 2.0
            dist = float(np.linalg.norm(agent.state.position - point)) - reach
            if dist < best_dist:
                best, best_dist = aid, dist
        return best

    def hold(self, agent_id: int) -> bool:
        if agent_id in self.agents and self.agents[agent_id].active:
            self.held.add(agent_id)
            return True
        return False

    def move_held(self, agent_id: int, x: float, y: float,
                  face_motion: bool = True) -> None:
        """Place a held agent, pointing it the way the hand is moving."""
        agent = self.agents.get(agent_id)
        if agent is None or agent_id not in self.held:
            return
        dx = float(x) - agent.state.x
        dy = float(y) - agent.state.y
        agent.state.x, agent.state.y = float(x), float(y)
        # A dragged vehicle should point where it is being dragged, but not spin
        # on tiny jitters, and the ego keeps its heading so the operator can
        # reposition it without re-aiming it.
        if face_motion and agent_id != EGO_ID and math.hypot(dx, dy) > 0.35:
            agent.state.heading = math.atan2(dy, dx)
        agent.state.speed = 0.0
        agent.state.lateral_speed = 0.0

    def release(self, agent_id: int) -> None:
        self.held.discard(agent_id)

    def despawn_near(self, x: float, y: float, radius: float = 3.0) -> int | None:
        """Remove the nearest road user to a point. Never the ego."""
        point = np.array([x, y], dtype=float)
        best, best_dist = None, radius
        for aid, agent in self.agents.items():
            if aid == EGO_ID or not agent.active:
                continue
            dist = float(np.linalg.norm(agent.state.position - point))
            if dist < best_dist:
                best, best_dist = aid, dist
        if best is not None:
            agent = self.agents[best]
            self.vanished.append((float(agent.state.x), float(agent.state.y)))
            agent.active = False
        return best

    def _next_agent_id(self) -> int:
        agent_id = self._next_id
        self._next_id += 1
        return agent_id

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
            ego_s=s_ego, impact_speed=self._impact_speed,
            ego_speed_at_impact=self._ego_speed_at_impact,
            impact_bearing_deg=self._impact_bearing)
        if verbose:
            print(metrics.summary())
        return RunResult(metrics, self.recorder if self.recorder.enabled else None,
                         wall)

    def step(self) -> None:
        # Anything removed since the last tick is reported before the controller
        # senses, so it never perceives what is no longer there.
        if self.vanished:
            self.controller.forget(self.vanished)
            self.vanished = []
        view = FrenetView(self.corridor, self.agents)

        # 1. Ego decision, timed. This is the PS's "replanning latency".
        t0 = time.perf_counter()
        cmd = self.controller.control(self.ego, view, self.corridor,
                                      self.t, self.dt)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        # Kept on the simulator so the live console can display it: the problem
        # statement asks for replanning latency, and a demo that cannot show the
        # number it claims is a demo a judge is right to distrust.
        self.last_replan_ms = latency_ms

        # 2. Every other road user acts. Traffic is oblivious to being simulated;
        #    it reacts to the ego only through ordinary NLB-IDM interactions.
        commands: dict[int, tuple[float, float]] = {}
        for aid, agent in self.agents.items():
            if aid == EGO_ID or not agent.active or aid in self.held:
                continue
            commands[aid] = self._agent_command(agent, view)

        # 3. Integrate. Ego first so its recorded state matches the command above.
        if EGO_ID not in self.held:
            self._integrate_ego(cmd)
        for aid, (accel, lateral) in commands.items():
            agent = self.agents[aid]
            agent.state = step_agent(agent.cls, agent.params, agent.state,
                                     accel, lateral, self.dt)

        self.t += self.dt
        self._keep_traffic_on_road()
        self._despawn()
        if self.live:
            self._admit_entries()
            self._reap()

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
            d_nominal_opposing=float(self.corridor.opposing_offset(s)),
            road_heading=float(self.corridor.reference.heading_at(s)),
            corridor_length=self.corridor.reference.length,
            speed_limit=self.corridor.comfortable_speed(agent.state.position,
                                                        agent.params.v_desired),
            dt=self.dt, time=self.t, ego=self.ego)
        return self.policies[agent.id].act(agent, ctx, self.rng)

    def _integrate_ego(self, cmd: ControlCommand) -> None:
        """Advance the ego, the only agent permitted to travel backwards.

        Backwards travel is unlocked by the command, never by the integrator.
        Lowering ``v_min`` unconditionally turns every hard brake into a reverse
        the moment the vehicle reaches a standstill - it rolls back under a
        braking command nobody meant as one - and that alone cost eight
        collision-free runs out of sixty, each with the ego struck while
        drifting backwards at a few centimetres a second.

        The reverse cap is walking pace. Nothing here reverses at speed, and a
        vehicle that could would be a worse hazard than the blockage it is
        escaping.
        """
        p = self.ego.params
        self.ego.state = step_bicycle(
            self.ego.state, cmd.accel, cmd.steer, self.dt,
            wheelbase_for(p), v_max=p.v_desired * 1.6,
            v_min=-EGO_REVERSE_LIMIT if cmd.reverse else 0.0)

    def _keep_traffic_on_road(self) -> None:
        """Hold other road users inside the drivable band.

        Their lateral controller is a soft repulsion from the verge, which a hard
        enough push - a lane change into them, a cow stopping dead - can overcome.
        Real traffic does not drive into the ditch, and a two-wheeler wandering
        across a field is the single most damaging thing a viewer can see, because
        it makes every other behaviour look arbitrary too.
        """
        ref = self.corridor.reference
        for aid, agent in self.agents.items():
            if aid == EGO_ID or not agent.active or agent.is_static:
                continue
            s, d = ref.to_frenet(agent.state.position)
            self._hold_heading(agent, aid, s)
            lo, hi = self.corridor.hard_bounds_at(s)
            half_w = agent.params.width / 2.0
            lo, hi = float(lo) + half_w, float(hi) - half_w
            if lo > hi:                       # road narrower than the vehicle
                lo = hi = 0.5 * (lo + hi)
            if lo <= d <= hi:
                continue
            clamped = min(max(d, lo), hi)
            pos = ref.to_cartesian(s, clamped)
            agent.state.x, agent.state.y = float(pos[0]), float(pos[1])
            # Bleed off the lateral velocity that carried it out, so it settles
            # against the edge instead of grinding along it.
            agent.state.lateral_speed = 0.0

    def _hold_heading(self, agent: Agent, agent_id: int, s: float) -> None:
        """Keep a road user pointed roughly along its direction of travel.

        The lateral controller reaches a desired offset by steering, and the steer
        needed grows as speed falls - so a slow agent with a distant lateral target
        can accumulate a full U-turn and then drive away in the wrong direction,
        which is what happened to the wrong-way riders. Traffic does not make
        U-turns mid-block; clamping the heading to a generous cone around the road
        makes that structurally impossible rather than a matter of tuning.

        Pedestrians and animals are exempt: turning on the spot is exactly what
        they do.
        """
        from ..core.kinematics import HOLONOMIC_CLASSES

        if agent.cls in HOLONOMIC_CLASSES:
            return
        policy = self.policies.get(agent_id)
        direction = getattr(policy, "direction", 1)
        road = float(self.corridor.reference.heading_at(s))
        if direction < 0:
            road += math.pi
        error = float(np.arctan2(np.sin(agent.state.heading - road),
                                 np.cos(agent.state.heading - road)))
        if abs(error) <= HEADING_CONE:
            return
        agent.state.heading = float(
            road + math.copysign(HEADING_CONE, error))

    def _despawn(self) -> None:
        length = self.corridor.reference.length
        ref = self.corridor.reference
        for aid, agent in self.agents.items():
            if aid == EGO_ID or not agent.active:
                continue
            s, d = ref.to_frenet(agent.state.position)
            if agent.state.speed <= 0.1:
                continue          # standing still: it has not driven anywhere
            # Agents pinned at either end of the corridor have driven off it.
            if s <= 0.05:
                self._retire(aid, agent, float(d), length - FLOW_ENTRY_INSET)
            elif s >= length - 0.05:
                self._retire(aid, agent, float(d), FLOW_ENTRY_INSET)

    def _retire(self, aid: int, agent: Agent, d: float, re_enter_s: float) -> None:
        """Take a road user off the far end of the road, and queue its return.

        Traffic is a *flow*, not a fixed cast. Before this, every road user that
        reached the end of the corridor was deactivated and nothing replaced it,
        so a live session's population could only fall. On the one-way merge
        scenario - where everything moves, and everything therefore eventually
        reaches the end - it fell from 26 road users to 1 over five minutes, and
        by the first lap completion at t=138 s only 2 were left. That is the
        emptied-out second lap: the ego respawns at the start of a road the
        traffic has already driven off the end of.

        A departure at one end is now an arrival at the other, which conserves
        the density the scenario asked for instead of draining it.
        """
        agent.active = False
        self.held.discard(aid)
        if not self.live:
            return
        # It really has stopped existing, and no sensor can tell that from an
        # occlusion; say so, or perception coasts a ghost of it.
        self.vanished.append((float(agent.state.x), float(agent.state.y)))
        if len(self._pending_entry) >= FLOW_QUEUE_MAX:
            return
        policy, args = self._policy_specs.get(aid, ("traffic", {}))
        self._pending_entry.append({
            "cls": agent.cls, "policy": policy, "args": dict(args),
            "s": float(re_enter_s), "d": d,
            "speed": float(agent.state.speed),
            "aggression": float(agent.aggression),
        })

    def _admit_entries(self) -> None:
        """Let queued road users back in wherever there is room for them.

        Entering traffic waits for a gap rather than being forced in: a vehicle
        materialising inside another one, or in front of the ego, would be a
        collision the planner had no way to avoid and no business being scored on.
        """
        if not self._pending_entry:
            return
        ref = self.corridor.reference
        occupied = []
        for agent in self.agents.values():
            if not agent.active:
                continue
            s, d = ref.to_frenet(agent.state.position)
            occupied.append((float(s), float(d), agent.params.length / 2.0,
                             agent.params.width / 2.0))

        waiting: list[dict] = []
        for entry in self._pending_entry:
            p = params_for(entry["cls"])
            s = entry["s"]
            d_min, d_max = self.corridor.bounds_at(s)
            half_w = p.width / 2.0
            d = float(np.clip(entry["d"], float(d_min) + half_w,
                              float(d_max) - half_w))
            clear = all(
                abs(s - os_) >= p.length / 2.0 + ohl + FLOW_ENTRY_GAP or
                abs(d - od) >= half_w + ohw + 0.6
                for os_, od, ohl, ohw in occupied)
            if not clear:
                waiting.append(entry)
                continue
            pos = ref.to_cartesian(s, d)
            heading = float(ref.heading_at(s))
            if entry["policy"] in ("oncoming", "wrong_way"):
                heading = float(heading + math.pi)
            aid = self._next_agent_id()
            self.agents[aid] = Agent(
                id=aid, cls=entry["cls"],
                state=State(float(pos[0]), float(pos[1]), heading,
                            entry["speed"]),
                aggression=entry["aggression"])
            self.policies[aid] = build_policy(entry["policy"], **entry["args"])
            self._policy_specs[aid] = (entry["policy"], entry["args"])
            occupied.append((s, d, p.length / 2.0, half_w))
        self._pending_entry = waiting

    def _reap(self) -> None:
        """Sweep up retired agents so a long live session does not accumulate them.

        Every tick iterates the whole world dict, so leaving the dead in it makes
        an hour-long session progressively slower for no reason. Ids are never
        reused, so nothing that still holds one can be handed the wrong vehicle.
        """
        dead = [aid for aid, agent in self.agents.items()
                if aid != EGO_ID and not agent.active]
        if len(dead) < REAP_THRESHOLD:
            return
        for aid in dead:
            del self.agents[aid]
            self.policies.pop(aid, None)
            self._policy_specs.pop(aid, None)
            # Erasing and contact-clearing do not go through _retire, so a
            # held id can still be sitting here; the viewer would be told it
            # is holding something that no longer exists.
            self.held.discard(aid)

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
                impact = float(np.linalg.norm(
                    other.state.velocity - self.ego.state.velocity))
                if self.live:
                    self._record_event("contact", f"{other.cls.value} "
                                       f"at {impact:.1f} m/s")
                    # Clear it so we are not left wedged inside it - and say so,
                    # or perception coasts a ghost of it right beside the ego.
                    self.vanished.append((float(other.state.x),
                                          float(other.state.y)))
                    other.active = False
                    continue
                self.outcome = "collision"
                self._collision_with = other.cls.value
                self._impact_speed = impact
                self._ego_speed_at_impact = float(self.ego.state.speed)
                rel = other.state.position - ego_p
                self._impact_bearing = math.degrees(wrap_to_pi(
                    math.atan2(float(rel[1]), float(rel[0]))
                    - float(self.ego.state.heading)))
                self.finished = True
                return

        d_min, d_max = self.corridor.hard_bounds_at(s)
        half_w = self.ego.params.width / 2.0
        if d > float(d_max) - half_w + OFF_ROAD_TOLERANCE or \
                d < float(d_min) + half_w - OFF_ROAD_TOLERANCE:
            if self.live:
                self._record_event("off road", f"{abs(d):.1f} m from centre")
                self._recentre_ego(s)
            else:
                self.outcome = "off_road"
                self.finished = True
            return

        if s >= self.scenario.goal_s:
            if self.live:
                self._record_event("goal reached", f"{s:.0f} m")
                self._add_ego()
                self.controller.reset(self.scenario)
            else:
                self.outcome = "goal"
                self.finished = True

    def _record_event(self, kind: str, detail: str) -> None:
        self.events.append({"t": round(self.t, 1), "kind": kind, "detail": detail})
        del self.events[:-12]

    def _recentre_ego(self, s: float) -> None:
        """Put the ego back on the carriageway without restarting the scene."""
        ref = self.corridor.reference
        pos = ref.to_cartesian(s, float(self.corridor.nominal_offset(s)))
        self.ego.state.x, self.ego.state.y = float(pos[0]), float(pos[1])
        self.ego.state.heading = float(ref.heading_at(s))
        self.ego.state.speed = 0.0

    def _ego_frenet_fallback(self) -> tuple[float, float]:
        return self.corridor.reference.to_frenet(self.ego.state.position)
