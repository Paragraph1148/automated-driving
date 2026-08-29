"""Core value types and the Indian road-user taxonomy.

The taxonomy is the foundation of the whole system: perception classifies into it,
prediction conditions its intent priors on it, and the risk field shapes its kernels
from it. Parameter values are drawn from published studies of heterogeneous
disordered traffic (HDT) in India; see docs/02-novelty.md for provenance.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Iterable

import numpy as np


class AgentClass(str, Enum):
    """Road users that actually appear on an Indian road."""

    CAR = "car"
    BUS = "bus"
    TRUCK = "truck"
    AUTO_RICKSHAW = "auto_rickshaw"
    TWO_WHEELER = "two_wheeler"
    BICYCLE = "bicycle"
    PEDESTRIAN = "pedestrian"
    PUSHCART = "pushcart"
    CATTLE = "cattle"
    STRAY_DOG = "stray_dog"
    BARRICADE = "barricade"
    PARKED_VEHICLE = "parked_vehicle"

    @property
    def is_static(self) -> bool:
        return self in (AgentClass.BARRICADE, AgentClass.PARKED_VEHICLE)

    @property
    def is_vru(self) -> bool:
        """Vulnerable road user - never to be nudged past at speed."""
        return self in (
            AgentClass.PEDESTRIAN,
            AgentClass.BICYCLE,
            AgentClass.TWO_WHEELER,
            AgentClass.CATTLE,
            AgentClass.STRAY_DOG,
            AgentClass.PUSHCART,
        )


@dataclass(frozen=True)
class ClassParams:
    """Physical and behavioural parameters for one road-user class.

    Longitudinal terms feed the IDM core; ``min_lateral_clearance`` and
    ``lateral_agility`` feed the non-lane-based extensions; ``predictability`` and
    ``lateral_sigma_rate`` feed prediction and the risk field.
    """

    length: float               # m
    width: float                # m
    v_desired: float            # m/s, free-flow desired speed
    a_max: float                # m/s^2, comfortable acceleration
    b_comf: float               # m/s^2, comfortable deceleration (positive)
    b_max: float                # m/s^2, emergency deceleration (positive)
    s0: float                   # m, minimum bumper-to-bumper gap at standstill
    T: float                    # s, desired time headway
    min_lateral_clearance: float  # m, lateral gap this class insists on
    lateral_agility: float      # m/s, max achievable lateral speed
    predictability: float       # 0..1, 1 = perfectly predictable
    lateral_sigma_rate: float   # m/s, growth of lateral position uncertainty
    yields_to_ego: float        # 0..1, probability this class gives way


# Physical dimensions follow Indian Roads Congress design-vehicle sizes; headway and
# gap-acceptance values follow HDT field studies (two-wheelers accept markedly
# smaller longitudinal and lateral gaps than cars, which is exactly why lane-based
# planners mispredict them).
CLASS_PARAMS: dict[AgentClass, ClassParams] = {
    AgentClass.CAR: ClassParams(
        4.2, 1.70, 13.9, 2.0, 3.0, 7.0, 2.0, 1.20, 0.50, 1.2, 0.80, 0.15, 0.55),
    AgentClass.BUS: ClassParams(
        11.0, 2.60, 11.1, 0.8, 2.2, 5.5, 3.0, 1.60, 0.70, 0.5, 0.90, 0.08, 0.20),
    AgentClass.TRUCK: ClassParams(
        8.0, 2.50, 11.1, 0.7, 2.0, 5.0, 3.0, 1.70, 0.70, 0.5, 0.88, 0.09, 0.20),
    AgentClass.AUTO_RICKSHAW: ClassParams(
        2.6, 1.40, 11.1, 1.6, 3.2, 6.0, 1.5, 0.90, 0.35, 1.6, 0.55, 0.30, 0.35),
    AgentClass.TWO_WHEELER: ClassParams(
        1.9, 0.70, 15.3, 2.5, 3.5, 7.5, 1.0, 0.60, 0.25, 2.4, 0.40, 0.45, 0.30),
    AgentClass.BICYCLE: ClassParams(
        1.7, 0.60, 4.5, 0.8, 2.0, 3.5, 0.8, 0.90, 0.30, 1.0, 0.55, 0.30, 0.40),
    AgentClass.PEDESTRIAN: ClassParams(
        0.60, 0.50, 1.4, 1.0, 2.0, 3.0, 0.4, 0.60, 0.30, 1.2, 0.35, 0.50, 0.45),
    AgentClass.PUSHCART: ClassParams(
        2.2, 1.00, 1.3, 0.4, 1.0, 1.8, 0.8, 1.20, 0.40, 0.4, 0.70, 0.18, 0.30),
    # Cattle are the canonical Indian hazard: slow, large, and almost unpredictable.
    AgentClass.CATTLE: ClassParams(
        2.2, 0.90, 1.6, 1.0, 1.5, 2.5, 0.5, 1.00, 0.60, 1.0, 0.15, 0.70, 0.05),
    AgentClass.STRAY_DOG: ClassParams(
        0.80, 0.30, 4.0, 3.0, 3.5, 5.0, 0.3, 0.50, 0.25, 3.0, 0.10, 0.90, 0.10),
    AgentClass.BARRICADE: ClassParams(
        1.2, 0.40, 0.0, 0.0, 0.0, 0.0, 0.3, 0.00, 0.30, 0.0, 1.00, 0.00, 0.00),
    AgentClass.PARKED_VEHICLE: ClassParams(
        4.2, 1.70, 0.0, 0.0, 0.0, 0.0, 0.3, 0.00, 0.40, 0.0, 1.00, 0.00, 0.00),
}


def params_for(cls: AgentClass) -> ClassParams:
    return CLASS_PARAMS[cls]


def wrap_to_pi(angle: float | np.ndarray) -> float | np.ndarray:
    """Wrap an angle (or array of angles) to (-pi, pi]."""
    return (np.asarray(angle) + np.pi) % (2.0 * np.pi) - np.pi


@dataclass
class State:
    """Planar rigid-body state of a road user.

    ``s``/``d`` are the Frenet coordinates against the active reference path and are
    filled in lazily by whatever holds a reference path; they are not authoritative.
    """

    x: float
    y: float
    heading: float          # rad, world frame
    speed: float            # m/s, along heading
    yaw_rate: float = 0.0   # rad/s
    accel: float = 0.0      # m/s^2, along heading
    lateral_speed: float = 0.0  # m/s, body-lateral (non-lane-based agents use this)

    @property
    def position(self) -> np.ndarray:
        return np.array([self.x, self.y], dtype=float)

    @property
    def velocity(self) -> np.ndarray:
        """World-frame velocity, including the body-lateral component."""
        c, s = math.cos(self.heading), math.sin(self.heading)
        return np.array([
            self.speed * c - self.lateral_speed * s,
            self.speed * s + self.lateral_speed * c,
        ], dtype=float)

    def copy(self) -> "State":
        return replace(self)


@dataclass
class Footprint:
    """Oriented rectangle used for collision checks and LiDAR ray-casting."""

    length: float
    width: float

    def corners(self, state: State, inflate: float = 0.0) -> np.ndarray:
        """Return the 4 world-frame corners, optionally inflated by a margin."""
        hl = self.length / 2.0 + inflate
        hw = self.width / 2.0 + inflate
        local = np.array([[hl, hw], [hl, -hw], [-hl, -hw], [-hl, hw]])
        c, s = math.cos(state.heading), math.sin(state.heading)
        rot = np.array([[c, -s], [s, c]])
        return local @ rot.T + state.position

    def radius(self) -> float:
        """Circumscribing radius - cheap broad-phase rejection."""
        return 0.5 * math.hypot(self.length, self.width)


@dataclass
class Agent:
    """A single road user in the world (the ego is agent id 0)."""

    id: int
    cls: AgentClass
    state: State
    params: ClassParams = field(init=False)
    footprint: Footprint = field(init=False)
    # Free-form per-policy memory (target speeds, intent timers, and so on).
    memory: dict = field(default_factory=dict)
    # Multiplies desired speed and shrinks accepted gaps; drawn per-agent so a
    # scenario contains a distribution of driver aggression, not one archetype.
    aggression: float = 1.0
    active: bool = True

    def __post_init__(self) -> None:
        self.params = params_for(self.cls)
        self.footprint = Footprint(self.params.length, self.params.width)

    @property
    def is_static(self) -> bool:
        return self.cls.is_static

    def corners(self, inflate: float = 0.0) -> np.ndarray:
        return self.footprint.corners(self.state, inflate)


def bounding_radius(agents: Iterable[Agent]) -> float:
    return max((a.footprint.radius() for a in agents), default=0.0)
