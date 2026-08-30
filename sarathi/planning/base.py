"""Controller interface shared by every ego planner.

Keeping this narrow means the baseline planner, the full SARATHI stack and any
ablation variant are interchangeable in the simulator and in the campaign runner -
which is what makes the A/B study in the report a one-line change rather than a
parallel code path.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ControlCommand:
    """What a planner hands to the vehicle each tick."""

    accel: float                      # m/s^2, longitudinal
    steer: float                      # rad, front-wheel angle
    debug: dict = field(default_factory=dict)


class EgoController:
    """Base class for ego planners."""

    name = "controller"

    def reset(self, scenario) -> None:
        """Called once before a run. Override to build maps, paths, caches."""

    def control(self, ego, view, corridor, t: float, dt: float) -> ControlCommand:
        raise NotImplementedError
