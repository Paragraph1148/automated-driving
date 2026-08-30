"""Scenario specification and loading.

A scenario is a YAML file. The same file is consumed by the Python engine and by
the MATLAB importer, so a scenario authored by a non-coder drives both runtimes and
produces comparable metrics.

Two design choices matter:

* **Geometry is authored in (s, d), not (x, y).** "A pothole 45 m along, 0.8 m left
  of centre" is a thing a person can write down and check against a photograph.
* **``chaos`` is a single scalar in [0,1] that modulates the whole scene** - how many
  wrong-way riders, how aggressive the drivers, how visible the lane markings, how
  many cattle. That is what makes both the Monte-Carlo campaign and the live demo
  slider possible from one spec.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml

from ..core.types import Agent, AgentClass, State, params_for
from .corridor import Corridor, SurfaceDefect

# Background-traffic composition, roughly matching observed mode shares on Indian
# urban roads: two-wheelers dominate, then cars, then autos, with a long tail.
DEFAULT_MIX = {
    AgentClass.TWO_WHEELER: 0.42,
    AgentClass.CAR: 0.22,
    AgentClass.AUTO_RICKSHAW: 0.16,
    AgentClass.BICYCLE: 0.08,
    AgentClass.BUS: 0.05,
    AgentClass.TRUCK: 0.04,
    AgentClass.PUSHCART: 0.03,
}

MARKET_MIX = {
    AgentClass.TWO_WHEELER: 0.38,
    AgentClass.PEDESTRIAN: 0.20,
    AgentClass.AUTO_RICKSHAW: 0.18,
    AgentClass.PUSHCART: 0.10,
    AgentClass.BICYCLE: 0.08,
    AgentClass.CAR: 0.06,
}

HIGHWAY_MIX = {
    AgentClass.TRUCK: 0.30,
    AgentClass.CAR: 0.26,
    AgentClass.TWO_WHEELER: 0.20,
    AgentClass.BUS: 0.14,
    AgentClass.AUTO_RICKSHAW: 0.10,
}

MIXES = {"default": DEFAULT_MIX, "market": MARKET_MIX, "highway": HIGHWAY_MIX}


@dataclass
class AgentSpec:
    cls: AgentClass
    policy: str
    s: float
    d: float
    speed: float = 0.0
    aggression: float = 1.0
    args: dict = field(default_factory=dict)


@dataclass
class FlowSpec:
    """Procedurally generated background traffic."""

    density: float = 0.05        # agents per metre of corridor
    mix: str = "default"
    direction: int = +1          # +1 with the ego, -1 oncoming
    speed_scale: float = 1.0


@dataclass
class ChaosEffects:
    """How the chaos scalar deforms the scene. All counts are at chaos = 1."""

    wrong_way_riders: int = 3
    extra_cattle: int = 2
    barricades: int = 3
    rash_fraction: float = 0.35
    aggression_gain: float = 0.45
    pedestrian_recklessness: float = 0.8
    marking_degradation: float = 1.0
    density_gain: float = 0.6


@dataclass
class Scenario:
    name: str
    description: str
    corridor: Corridor
    ego: dict
    agents: list[AgentSpec]
    flows: list[FlowSpec]
    chaos: float
    chaos_effects: ChaosEffects
    duration: float
    dt: float
    seed: int
    goal_s: float
    #: Sensor visibility multiplier: 1.0 clear daylight, lower for dust, glare,
    #: rain or night. Scales every detection probability and noise term.
    visibility: float = 1.0
    tags: list[str] = field(default_factory=list)


# -- geometry -------------------------------------------------------------
def build_centreline(spec: dict) -> np.ndarray:
    """Build a centreline polyline from a list of straight and arc segments."""
    segments = spec.get("segments") or [{"type": "straight",
                                         "length": spec.get("length", 200.0)}]
    pts = [np.array([0.0, 0.0])]
    heading = math.radians(float(spec.get("start_heading_deg", 0.0)))
    step = 0.5
    for seg in segments:
        kind = seg.get("type", "straight")
        if kind == "straight":
            n = max(1, int(round(float(seg["length"]) / step)))
            for _ in range(n):
                pts.append(pts[-1] + step * np.array([math.cos(heading),
                                                      math.sin(heading)]))
        elif kind == "arc":
            radius = float(seg["radius"])
            sweep = math.radians(float(seg["angle_deg"]))
            n = max(2, int(round(abs(radius * sweep) / step)))
            dtheta = sweep / n
            for _ in range(n):
                heading += dtheta
                pts.append(pts[-1] + abs(radius * dtheta) *
                           np.array([math.cos(heading), math.sin(heading)]))
        else:
            raise ValueError(f"unknown segment type {kind!r}")
    return np.array(pts)


def _defect_to_world(corridor_ref, s: float, d: float) -> tuple[float, float]:
    p = corridor_ref.to_cartesian(float(s), float(d))
    return float(p[0]), float(p[1])


# -- loading --------------------------------------------------------------
def load_scenario(path: str | Path, chaos: float | None = None,
                  seed: int | None = None) -> Scenario:
    """Load a scenario YAML, optionally overriding chaos and seed.

    The override is what the campaign runner and the live demo slider both use.
    """
    raw = yaml.safe_load(Path(path).read_text())
    return scenario_from_dict(raw, chaos=chaos, seed=seed)


def scenario_from_dict(raw: dict, chaos: float | None = None,
                       seed: int | None = None) -> Scenario:
    chaos = float(raw.get("chaos", 0.3) if chaos is None else chaos)
    chaos = float(np.clip(chaos, 0.0, 1.0))
    effects = ChaosEffects(**(raw.get("chaos_effects") or {}))

    cspec = raw["corridor"]
    centreline = build_centreline(cspec.get("geometry", {}))
    width = [tuple(w) for w in cspec["width"]]
    corridor = Corridor.from_spec(
        centreline, width,
        road_type=cspec.get("road_type", "two_way"),
        lane_marking_quality=float(cspec.get("lane_marking_quality", 0.0)),
        surface_quality=float(cspec.get("surface_quality", 0.6)),
        drive_side=int(cspec.get("drive_side", 1)),
    )
    # Faded markings are one of the things chaos degrades.
    corridor.lane_marking_quality = float(np.clip(
        corridor.lane_marking_quality * (1.0 - effects.marking_degradation * chaos),
        0.0, 1.0))

    for dspec in cspec.get("defects", []) or []:
        x, y = _defect_to_world(corridor.reference, dspec["s"], dspec.get("d", 0.0))
        corridor.defects.append(SurfaceDefect(
            x=x, y=y, radius=float(dspec.get("radius", 1.0)),
            severity=float(dspec.get("severity", 0.5)),
            kind=dspec.get("kind", "pothole")))

    agents = [AgentSpec(
        cls=AgentClass(a["cls"]), policy=a.get("policy", "traffic"),
        s=float(a["s"]), d=float(a.get("d", 0.0)),
        speed=float(a.get("speed", 0.0)),
        aggression=float(a.get("aggression", 1.0)),
        args=a.get("args", {}) or {},
    ) for a in raw.get("agents", []) or []]

    flows = [FlowSpec(
        density=float(f.get("density", 0.05)), mix=f.get("mix", "default"),
        direction=int(f.get("direction", 1)),
        speed_scale=float(f.get("speed_scale", 1.0)),
    ) for f in raw.get("traffic_flow", []) or []]

    ego = raw.get("ego", {}) or {}
    goal_s = float(ego.get("goal_s", corridor.reference.length - 5.0))

    return Scenario(
        name=raw.get("name", "unnamed"),
        description=raw.get("description", ""),
        corridor=corridor,
        ego={"s": float(ego.get("s", 5.0)),
             "d": float(ego.get("d", float(corridor.nominal_offset(5.0)))),
             "speed": float(ego.get("speed", 6.0))},
        agents=agents, flows=flows, chaos=chaos, chaos_effects=effects,
        duration=float(raw.get("duration", 60.0)),
        dt=float(raw.get("dt", 0.05)),
        seed=int(raw.get("seed", 0) if seed is None else seed),
        goal_s=goal_s,
        visibility=float(raw.get("visibility", 1.0)),
        tags=list(raw.get("tags", []) or []),
    )


# -- population -----------------------------------------------------------
def populate(scenario: Scenario, rng: np.random.Generator
             ) -> tuple[dict[int, Agent], dict[int, tuple[str, dict]]]:
    """Instantiate every agent for a scenario, including chaos-driven additions.

    Returns ``(agents, policy_specs)`` where ``policy_specs[id]`` is the
    ``(policy_name, kwargs)`` pair the simulator should build for that agent.
    """
    corridor = scenario.corridor
    ref = corridor.reference
    chaos = scenario.chaos
    fx = scenario.chaos_effects

    agents: dict[int, Agent] = {}
    policies: dict[int, tuple[str, dict]] = {}
    next_id = 1

    # Footprints already claimed, as (s, d, half_length, half_width). Used to keep
    # procedural traffic from spawning inside another vehicle - or inside the ego,
    # which silently turns a planner benchmark into a spawn-collision benchmark.
    claimed: list[tuple[float, float, float, float]] = []

    def _is_free(s: float, d: float, half_l: float, half_w: float,
                 margin: float = 0.7) -> bool:
        for cs, cd, chl, chw in claimed:
            if abs(s - cs) < half_l + chl + margin and \
                    abs(d - cd) < half_w + chw + margin:
                return False
        return True

    def place(cls: AgentClass, s: float, d: float, speed: float,
              policy: str, args: dict, aggression: float) -> None:
        nonlocal next_id
        s = float(np.clip(s, 0.0, ref.length))
        p = params_for(cls)
        d_min, d_max = corridor.bounds_at(s)
        half_w = p.width / 2.0
        d = float(np.clip(d, float(d_min) + half_w, float(d_max) - half_w))
        pos = ref.to_cartesian(s, d)
        heading = float(ref.heading_at(s))
        if policy in ("oncoming", "wrong_way"):
            heading = float(heading + math.pi)
        agent = Agent(id=next_id, cls=cls,
                      state=State(float(pos[0]), float(pos[1]), heading,
                                  max(0.0, speed)),
                      aggression=aggression)
        agents[next_id] = agent
        policies[next_id] = (policy, args)
        claimed.append((s, d, p.length / 2.0, half_w))
        next_id += 1

    # Reserve the ego's footprint plus the headway it needs to be a fair test:
    # a scenario that starts the ego 1 m behind a stationary bus measures nothing.
    ego_s = float(scenario.ego["s"])
    ego_d = float(scenario.ego["d"])
    ego_p = params_for(AgentClass.CAR)
    claimed.append((ego_s + 9.0, ego_d, 13.0, ego_p.width / 2.0 + 1.2))

    # 1. Explicitly authored agents always appear, at any chaos level.
    for spec in scenario.agents:
        place(spec.cls, spec.s, spec.d, spec.speed, spec.policy,
              dict(spec.args), spec.aggression)

    # 2. Procedural background traffic.
    for flow in scenario.flows:
        mix = MIXES.get(flow.mix, DEFAULT_MIX)
        classes = list(mix)
        weights = np.array([mix[c] for c in classes], dtype=float)
        weights /= weights.sum()
        density = flow.density * (1.0 + fx.density_gain * chaos)
        count = int(rng.poisson(density * ref.length))
        for _ in range(count):
            cls = classes[int(rng.choice(len(classes), p=weights))]
            p = params_for(cls)
            # Rejection-sample a spot that is actually empty.
            spot = None
            # Leave room at the end the flow is heading toward: an agent spawned
            # 0.5 m from the corridor end despawns on the next tick, which reads
            # in the viewer as traffic that appears and freezes.
            margin = 18.0
            lo_s = margin if flow.direction < 0 else 0.0
            hi_s = ref.length - (margin if flow.direction > 0 else 0.0)
            for _attempt in range(16):
                s_try = float(rng.uniform(lo_s, max(lo_s + 1.0, hi_s)))
                d_min_t, d_max_t = corridor.bounds_at(s_try)
                lo_t, hi_t = (0.2, float(d_max_t)) if flow.direction > 0 else \
                             (float(d_min_t), -0.2)
                if hi_t - lo_t < p.width:
                    continue
                d_try = float(rng.uniform(lo_t + p.width / 2.0,
                                          hi_t - p.width / 2.0))
                if _is_free(s_try, d_try, p.length / 2.0, p.width / 2.0):
                    spot = (s_try, d_try)
                    break
            if spot is None:
                continue          # road is genuinely full here; do not force it
            s, d = spot
            d_min, d_max = corridor.bounds_at(s)
            aggression = float(np.clip(
                rng.normal(1.0 + fx.aggression_gain * chaos, 0.18), 0.55, 2.0))
            speed = p.v_desired * flow.speed_scale * float(rng.uniform(0.55, 0.95))
            rash = rng.random() < fx.rash_fraction * chaos and not cls.is_static
            if flow.direction < 0:
                policy, args = "oncoming", {}
            elif rash:
                policy, args = "rash", {}
            else:
                policy, args = "traffic", {}
            if cls is AgentClass.PEDESTRIAN:
                # Only some pedestrians are crossing. The rest walk *along* the
                # carriageway edge, which is what a market street actually looks
                # like - a scene of people all standing still waiting for a gap
                # reads as a frozen simulation, not a busy road.
                if rng.random() < 0.55:
                    policy = "pedestrian_crossing"
                    args = {"cross_to": float(rng.uniform(float(d_min),
                                                          float(d_max))),
                            "recklessness": fx.pedestrian_recklessness * chaos,
                            "start_delay": float(rng.uniform(0.0, 12.0))}
                    speed = 0.0
                else:
                    policy, args = "traffic", {}
                    speed = float(rng.uniform(0.7, 1.4))
                    d = float(np.clip(d, lo_t + 0.4, hi_t - 0.4))
            place(cls, s, d, speed, policy, args, aggression)

    # 3. Chaos-driven hazards. These are the specifically Indian failure modes,
    #    and they scale in with the slider rather than being all-or-nothing.
    def _place_hazard(cls: AgentClass, s_lo: float, s_hi: float,
                      d_fn, speed: float, policy: str, aggression: float) -> None:
        p = params_for(cls)
        for _attempt in range(16):
            s_try = float(rng.uniform(s_lo, s_hi))
            d_min_t, d_max_t = corridor.bounds_at(s_try)
            d_try = d_fn(float(d_min_t), float(d_max_t))
            if _is_free(s_try, d_try, p.length / 2.0, p.width / 2.0):
                place(cls, s_try, d_try, speed, policy, {}, aggression)
                return

    for _ in range(int(round(fx.wrong_way_riders * chaos))):
        _place_hazard(AgentClass.TWO_WHEELER, 0.25 * ref.length, ref.length,
                      lambda lo, hi: hi * 0.75, float(rng.uniform(6.0, 12.0)),
                      "wrong_way", float(rng.uniform(1.1, 1.6)))

    for _ in range(int(round(fx.extra_cattle * chaos))):
        _place_hazard(AgentClass.CATTLE, 0.2 * ref.length, 0.95 * ref.length,
                      lambda lo, hi: float(rng.uniform(lo + 1.0, hi - 1.0)),
                      0.0, "cattle", 1.0)

    for _ in range(int(round(fx.barricades * chaos))):
        _place_hazard(AgentClass.BARRICADE, 0.15 * ref.length, 0.95 * ref.length,
                      lambda lo, hi: (hi if rng.random() < 0.5 else lo) *
                                     float(rng.uniform(0.55, 0.9)),
                      0.0, "static", 1.0)

    return agents, policies
