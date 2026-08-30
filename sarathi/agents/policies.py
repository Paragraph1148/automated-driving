"""The Indian behaviour zoo.

NLB-IDM produces a plausible *traffic stream*. It does not by itself produce the
things that actually break an autonomous vehicle on an Indian road: a rider coming
at you the wrong way down a one-way stretch, an auto-rickshaw stopping dead to pick
up a fare, a cow that stands in the carriageway and then changes its mind, a
pedestrian who accepts a gap you would not have offered.

Each policy below is a distinct failure mode the planner must survive. They are the
scenario vocabulary the problem statement asks for, expressed as behaviour rather
than as scripted waypoints - so they still behave sensibly when the ego does
something the scenario author did not anticipate.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..core.types import Agent, AgentClass, wrap_to_pi
from .nlbidm import Neighbour, lateral_accel, longitudinal_accel

# Command returned by every policy: (longitudinal accel, lateral term).
# For wheeled classes the lateral term is a front-wheel steer angle; for holonomic
# classes it is a lateral acceleration. See core.kinematics.step_agent.
Command = tuple[float, float]


@dataclass
class PolicyContext:
    """Everything a policy is allowed to see."""

    s: float
    d: float
    s_dot: float
    d_dot: float
    neighbours: list[Neighbour]
    d_min: float
    d_max: float
    d_nominal: float
    corridor_length: float
    speed_limit: float
    dt: float
    time: float
    #: The ego vehicle, for policies that explicitly negotiate with it. Most
    #: policies ignore it: traffic reacts to the ego through ordinary NLB-IDM
    #: interactions, not because it knows which vehicle is under test.
    ego: "Agent | None" = None


class Policy:
    """Base class. Subclasses override :meth:`act`."""

    #: Direction of travel along the corridor: +1 with the reference, -1 against it.
    direction: int = +1

    def act(self, agent: Agent, ctx: PolicyContext,
            rng: np.random.Generator) -> Command:
        raise NotImplementedError

    # -- shared helpers ---------------------------------------------------
    def _lateral_to_control(self, agent: Agent, lat_accel: float) -> float:
        """Convert a desired lateral acceleration into this class's control input.

        Holonomic agents take it directly. Wheeled agents cannot slide sideways, so
        we convert it to a steer angle: at speed ``v`` a lateral acceleration ``a``
        needs curvature ``a/v^2``, hence steer ``atan(L * a / v^2)``.
        """
        from ..core.kinematics import HOLONOMIC_CLASSES, max_steer_for, wheelbase_for

        if agent.cls in HOLONOMIC_CLASSES:
            return lat_accel
        v = max(abs(agent.state.speed), 1.5)
        L = wheelbase_for(agent.params)
        steer = math.atan(L * lat_accel / (v * v))
        limit = max_steer_for(agent.cls)
        return float(np.clip(steer, -limit, limit))


class TrafficPolicy(Policy):
    """The ordinary Indian road user: NLB-IDM with gap seeking.

    Covers cars, buses, trucks, autos, two-wheelers and bicycles going with the
    flow. All of the character comes from the class parameters and the per-agent
    aggression draw, not from special-casing.
    """

    def __init__(self, target_offset: float | None = None,
                 gap_seeking: float = 1.0):
        self.target_offset = target_offset
        self.gap_seeking = gap_seeking

    def act(self, agent, ctx, rng):
        accel, gov = longitudinal_accel(
            agent.cls, agent.params, agent.aggression,
            ctx.s, ctx.d, ctx.s_dot, ctx.neighbours,
            v_desired=min(agent.params.v_desired, ctx.speed_limit))
        d_pref = ctx.d_nominal if self.target_offset is None else self.target_offset
        lat = lateral_accel(agent.cls, agent.params, agent.aggression,
                            ctx.s, ctx.d, ctx.s_dot, ctx.d_dot, ctx.neighbours,
                            d_pref, ctx.d_min, ctx.d_max,
                            gap_seeking=self.gap_seeking)
        agent.memory["governing"] = gov
        return accel, self._lateral_to_control(agent, lat)


class OncomingPolicy(TrafficPolicy):
    """Legitimate traffic travelling the other way down a two-way road.

    Runs against the reference direction, so its Frenet longitudinal coordinates are
    mirrored before NLB-IDM sees them - the model is direction-agnostic, we simply
    present the world from this agent's point of view.
    """

    direction = -1

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def act(self, agent, ctx, rng):
        mirrored = _mirror_context(ctx)
        accel, steer = super().act(agent, mirrored, rng)
        return accel, -steer


class WrongWayPolicy(TrafficPolicy):
    """A rider coming *at you* on your own side of the road.

    Endemic on Indian roads and one of the hardest cases for a lane-based planner,
    which will happily predict the rider into a lane they have no intention of
    using. Hugs the road edge, is largely oblivious to oncoming traffic, and only
    reacts very late.
    """

    direction = -1

    def __init__(self, edge_hug: float = 0.75, obliviousness: float = 0.7):
        super().__init__(gap_seeking=0.3)
        self.edge_hug = edge_hug
        self.obliviousness = obliviousness

    def act(self, agent, ctx, rng):
        mirrored = _mirror_context(ctx)
        # Hug the edge of the carriageway it is illegally occupying.
        edge = mirrored.d_max * self.edge_hug
        mirrored = _replace_ctx(mirrored, d_nominal=edge)
        # Largely ignores what is in front of it until very close.
        near = [nb for nb in mirrored.neighbours
                if (nb.s - mirrored.s) < 12.0 or rng.random() > self.obliviousness]
        mirrored = _replace_ctx(mirrored, neighbours=near)
        self.target_offset = edge
        accel, steer = super().act(agent, mirrored, rng)
        return accel, -steer


class RashDriverPolicy(TrafficPolicy):
    """Aggressive tailgating with abrupt, unsignalled lateral darts.

    Periodically commits to a hard lateral move regardless of whether the gap is
    actually acceptable, then holds it for a second or two.
    """

    def __init__(self, dart_period: float = 4.0, dart_magnitude: float = 2.0):
        super().__init__(gap_seeking=1.8)
        self.dart_period = dart_period
        self.dart_magnitude = dart_magnitude

    def act(self, agent, ctx, rng):
        mem = agent.memory
        mem.setdefault("next_dart", ctx.time + rng.exponential(self.dart_period))
        mem.setdefault("dart_until", -1.0)
        mem.setdefault("dart_target", ctx.d_nominal)

        if ctx.time >= mem["next_dart"] and ctx.time > mem["dart_until"]:
            span = ctx.d_max - ctx.d_min
            offset = rng.choice([-1.0, 1.0]) * self.dart_magnitude
            mem["dart_target"] = float(np.clip(ctx.d + offset,
                                               ctx.d_min + 0.4, ctx.d_max - 0.4))
            mem["dart_until"] = ctx.time + rng.uniform(1.0, 2.5)
            mem["next_dart"] = ctx.time + rng.exponential(self.dart_period)

        if ctx.time <= mem["dart_until"]:
            self.target_offset = mem["dart_target"]
        else:
            self.target_offset = None
        return super().act(agent, ctx, rng)


class CattlePolicy(Policy):
    """A cow in the carriageway.

    The canonical Indian hazard, and the reason a single-hypothesis predictor is
    inadequate. Alternates between grazing (stationary), ambling, and occasionally
    changing its mind entirely. It does not model the ego at all - which is the
    point. Everything about it is low-predictability by construction.
    """

    def __init__(self, wander: float = 0.6, stop_bias: float = 0.45):
        self.wander = wander
        self.stop_bias = stop_bias

    def act(self, agent, ctx, rng):
        mem = agent.memory
        mem.setdefault("until", -1.0)
        mem.setdefault("mode", "graze")
        mem.setdefault("lat", 0.0)

        if ctx.time >= mem["until"]:
            mem["mode"] = "graze" if rng.random() < self.stop_bias else "amble"
            mem["until"] = ctx.time + rng.uniform(1.5, 5.0)
            mem["lat"] = float(rng.normal(0.0, self.wander))

        target_speed = 0.0 if mem["mode"] == "graze" else \
            float(rng.uniform(0.4, agent.params.v_desired))
        accel = float(np.clip((target_speed - ctx.s_dot) * 1.2,
                              -agent.params.b_comf, agent.params.a_max))
        lat = float(np.clip(mem["lat"] - 0.8 * ctx.d_dot,
                            -agent.params.lateral_agility,
                            agent.params.lateral_agility))
        # Only the corridor edge has any authority over a cow.
        if ctx.d > ctx.d_max - 0.5:
            lat -= 2.0
        if ctx.d < ctx.d_min + 0.5:
            lat += 2.0
        return accel, lat


class PedestrianCrossingPolicy(Policy):
    """A pedestrian crossing at an unmarked location, with gap acceptance.

    Waits at the kerb, evaluates the nearest approaching vehicle, and steps out once
    the estimated time gap exceeds its threshold. ``recklessness`` shrinks that
    threshold; at high values the pedestrian accepts gaps that force the ego to brake,
    which is exactly the "sudden pedestrian movement" the problem statement names.
    """

    def __init__(self, cross_to: float, trigger_gap: float = 3.5,
                 recklessness: float = 0.0, start_delay: float = 0.0):
        self.cross_to = cross_to
        self.trigger_gap = trigger_gap
        self.recklessness = recklessness
        self.start_delay = start_delay

    def act(self, agent, ctx, rng):
        mem = agent.memory
        mem.setdefault("crossing", False)

        if not mem["crossing"]:
            if ctx.time < self.start_delay:
                return -agent.params.b_comf, 0.0
            gap = self._time_gap(ctx)
            threshold = self.trigger_gap * (1.0 - 0.85 * self.recklessness)
            if gap > threshold:
                mem["crossing"] = True
            else:
                return float(np.clip(-ctx.s_dot * 2.0, -agent.params.b_comf, 0.0)), 0.0

        direction = 1.0 if self.cross_to > ctx.d else -1.0
        if abs(ctx.d - self.cross_to) < 0.3:
            direction = 0.0
        target_lat = direction * agent.params.v_desired
        lat = float(np.clip((target_lat - ctx.d_dot) * 2.0,
                            -agent.params.lateral_agility * 2,
                            agent.params.lateral_agility * 2))
        accel = float(np.clip((0.3 - ctx.s_dot) * 1.0, -agent.params.b_comf,
                              agent.params.a_max))
        return accel, lat

    def _time_gap(self, ctx: PolicyContext) -> float:
        """Estimated seconds until the nearest approaching vehicle arrives."""
        best = float("inf")
        for nb in ctx.neighbours:
            if nb.cls in (AgentClass.PEDESTRIAN, AgentClass.STRAY_DOG):
                continue
            ds = nb.s - ctx.s
            closing = -nb.s_dot if ds > 0 else nb.s_dot
            if closing <= 0.2:
                continue
            best = min(best, abs(ds) / closing)
        return best


class SuddenStopPolicy(TrafficPolicy):
    """An auto-rickshaw or bus that stops dead to pick up or drop a passenger.

    No indication, no pull-in. Triggers once, at a given arc length.
    """

    def __init__(self, stop_at_s: float, hold: float = 6.0):
        super().__init__(gap_seeking=0.6)
        self.stop_at_s = stop_at_s
        self.hold = hold

    def act(self, agent, ctx, rng):
        mem = agent.memory
        mem.setdefault("stopped_at", None)
        if mem["stopped_at"] is None and ctx.s >= self.stop_at_s:
            mem["stopped_at"] = ctx.time
        if mem["stopped_at"] is not None and \
                ctx.time - mem["stopped_at"] < self.hold:
            accel = float(np.clip(-ctx.s_dot * 3.0, -agent.params.b_max, 0.0))
            return accel, 0.0
        return super().act(agent, ctx, rng)


class CrossTrafficPolicy(Policy):
    """Traffic crossing the ego's corridor at an unsignalised junction.

    Corridor-Frenet coordinates are degenerate for motion perpendicular to the
    corridor, so this policy drives its agent along a fixed world heading instead
    and negotiates directly with the ego.

    The negotiation is the point. At an Indian junction without signals nobody has
    priority in practice: vehicles creep into the conflict zone and the outcome is
    settled by who commits first. ``assertiveness`` is the probability this agent
    refuses to yield, so the ego cannot simply assume it will be given way.
    """

    def __init__(self, heading_deg: float, target_speed: float = 6.0,
                 assertiveness: float = 0.5, conflict_radius: float = 18.0):
        self.heading = math.radians(heading_deg)
        self.target_speed = target_speed
        self.assertiveness = assertiveness
        self.conflict_radius = conflict_radius

    def act(self, agent, ctx, rng):
        mem = agent.memory
        if "committed" not in mem:
            mem["committed"] = rng.random() < self.assertiveness

        target = self.target_speed
        if ctx.ego is not None:
            to_ego = ctx.ego.state.position - agent.state.position
            dist = float(np.linalg.norm(to_ego))
            if dist < self.conflict_radius:
                closing = float(np.dot(ctx.ego.state.velocity -
                                       agent.state.velocity, to_ego)) < 0.0
                if closing and not mem["committed"]:
                    # Creep, do not stop dead - stopping fully is not how this
                    # junction works, and an ego that expects it will be stuck.
                    target = 0.8 if dist > 7.0 else 0.0
                elif closing:
                    target = self.target_speed * 1.1

        accel = float(np.clip((target - agent.state.speed) * 1.5,
                              -agent.params.b_max, agent.params.a_max))
        # Hold the crossing heading with a proportional steer.
        from ..core.kinematics import max_steer_for
        err = float(wrap_to_pi(self.heading - agent.state.heading))
        steer = float(np.clip(1.2 * err, -max_steer_for(agent.cls),
                              max_steer_for(agent.cls)))
        return accel, steer


class StaticPolicy(Policy):
    """Barricades, parked vehicles, and roadside encroachment - they do not move."""

    def act(self, agent, ctx, rng):
        return 0.0, 0.0


POLICY_REGISTRY = {
    "traffic": TrafficPolicy,
    "oncoming": OncomingPolicy,
    "wrong_way": WrongWayPolicy,
    "rash": RashDriverPolicy,
    "cattle": CattlePolicy,
    "pedestrian_crossing": PedestrianCrossingPolicy,
    "sudden_stop": SuddenStopPolicy,
    "cross_traffic": CrossTrafficPolicy,
    "static": StaticPolicy,
}


def build_policy(name: str, **kwargs) -> Policy:
    if name not in POLICY_REGISTRY:
        raise KeyError(f"unknown policy {name!r}; "
                       f"known: {sorted(POLICY_REGISTRY)}")
    return POLICY_REGISTRY[name](**kwargs)


def _mirror_context(ctx: PolicyContext) -> PolicyContext:
    """Present the world to an agent travelling against the reference direction.

    Mirroring ``s`` and ``d`` lets a single direction-agnostic NLB-IDM serve both
    directions of travel, instead of maintaining two near-duplicate models.
    """
    return PolicyContext(
        s=-ctx.s, d=-ctx.d, s_dot=-ctx.s_dot, d_dot=-ctx.d_dot,
        neighbours=[Neighbour(nb.id, nb.cls, -nb.s, -nb.d, -nb.s_dot, -nb.d_dot,
                              nb.half_length, nb.half_width)
                    for nb in ctx.neighbours],
        d_min=-ctx.d_max, d_max=-ctx.d_min, d_nominal=-ctx.d_nominal,
        corridor_length=ctx.corridor_length, speed_limit=ctx.speed_limit,
        dt=ctx.dt, time=ctx.time, ego=ctx.ego)


def _replace_ctx(ctx: PolicyContext, **kwargs) -> PolicyContext:
    from dataclasses import replace
    return replace(ctx, **kwargs)
