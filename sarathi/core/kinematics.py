"""Motion models.

Wheeled road users use a kinematic bicycle model (the same model we mirror in the
Simulink port, so trajectories are comparable between runtimes). Pedestrians and
animals use a holonomic model, because they genuinely are holonomic and forcing a
bicycle model on a cow is exactly the kind of modelling error that makes a planner
overconfident about where it will be in two seconds.
"""
from __future__ import annotations

import math

import numpy as np

from .types import AgentClass, ClassParams, State, wrap_to_pi

HOLONOMIC_CLASSES = frozenset({
    AgentClass.PEDESTRIAN, AgentClass.CATTLE, AgentClass.STRAY_DOG,
    AgentClass.PUSHCART,
})


def wheelbase_for(params: ClassParams) -> float:
    """Wheelbase approximated as 60% of body length - adequate for all classes here."""
    return max(0.5, 0.6 * params.length)


def step_bicycle(state: State, accel: float, steer: float, dt: float,
                 wheelbase: float, v_max: float, v_min: float = 0.0) -> State:
    """Advance a kinematic bicycle by ``dt`` using midpoint integration.

    ``steer`` is the front-wheel angle in radians. Midpoint (rather than Euler)
    integration matters here: at 20 Hz with a 30 deg steer, Euler accumulates
    visible cross-track drift over a few seconds of simulation.
    """
    v0 = state.speed
    v_half = float(np.clip(v0 + 0.5 * accel * dt, v_min, v_max))
    yaw_rate = v_half * math.tan(steer) / wheelbase
    th_half = state.heading + 0.5 * yaw_rate * dt

    v1 = float(np.clip(v0 + accel * dt, v_min, v_max))
    return State(
        x=state.x + v_half * math.cos(th_half) * dt,
        y=state.y + v_half * math.sin(th_half) * dt,
        heading=float(wrap_to_pi(state.heading + yaw_rate * dt)),
        speed=v1,
        yaw_rate=yaw_rate,
        accel=accel,
        lateral_speed=0.0,
    )


def step_holonomic(state: State, accel: float, lateral_accel: float, dt: float,
                   v_max: float, lateral_v_max: float) -> State:
    """Advance a holonomic agent (pedestrian, animal, handcart).

    Heading tracks the resultant velocity so the rendered footprint and any
    heading-conditioned risk kernel stay consistent with where the agent is going.
    """
    v = float(np.clip(state.speed + accel * dt, 0.0, v_max))
    vl = float(np.clip(state.lateral_speed + lateral_accel * dt,
                       -lateral_v_max, lateral_v_max))
    c, s = math.cos(state.heading), math.sin(state.heading)
    vx = v * c - vl * s
    vy = v * s + vl * c
    speed_mag = math.hypot(vx, vy)
    heading = math.atan2(vy, vx) if speed_mag > 1e-3 else state.heading
    return State(
        x=state.x + vx * dt,
        y=state.y + vy * dt,
        heading=float(wrap_to_pi(heading)),
        speed=speed_mag,
        yaw_rate=float(wrap_to_pi(heading - state.heading)) / dt if dt > 0 else 0.0,
        accel=accel,
        lateral_speed=0.0 if speed_mag > 1e-3 else vl,
    )


def step_agent(cls: AgentClass, params: ClassParams, state: State,
               accel: float, steer_or_lateral: float, dt: float) -> State:
    """Dispatch to the right motion model for ``cls``.

    For wheeled classes ``steer_or_lateral`` is a front-wheel angle (rad); for
    holonomic classes it is a lateral acceleration (m/s^2).
    """
    if cls.is_static:
        return state.copy()
    if cls in HOLONOMIC_CLASSES:
        return step_holonomic(state, accel, steer_or_lateral, dt,
                              v_max=params.v_desired * 1.6,
                              lateral_v_max=params.lateral_agility)
    max_steer = max_steer_for(cls)
    steer = float(np.clip(steer_or_lateral, -max_steer, max_steer))
    return step_bicycle(state, accel, steer, dt, wheelbase_for(params),
                        v_max=params.v_desired * 1.6)


def max_steer_for(cls: AgentClass) -> float:
    """Front-wheel steering limit. Two-wheelers and autos out-turn everything else."""
    return {
        AgentClass.TWO_WHEELER: math.radians(45.0),
        AgentClass.BICYCLE: math.radians(50.0),
        AgentClass.AUTO_RICKSHAW: math.radians(40.0),
        AgentClass.CAR: math.radians(33.0),
        AgentClass.BUS: math.radians(22.0),
        AgentClass.TRUCK: math.radians(24.0),
    }.get(cls, math.radians(35.0))


def steer_towards(state: State, target: np.ndarray, wheelbase: float,
                  lookahead: float, max_steer: float) -> float:
    """Pure-pursuit steering command toward a world-frame target point."""
    dx = target[0] - state.x
    dy = target[1] - state.y
    alpha = wrap_to_pi(math.atan2(dy, dx) - state.heading)
    ld = max(lookahead, 1e-3)
    steer = math.atan2(2.0 * wheelbase * math.sin(alpha), ld)
    return float(np.clip(steer, -max_steer, max_steer))
