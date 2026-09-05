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
    #: Whether the planner is deliberately reversing this tick.
    #:
    #: Reverse has to be *asked for*, not merely permitted by the integrator.
    #: Allowing negative speed unconditionally turns every hard brake into a
    #: reverse the moment the vehicle reaches a standstill: it rolls backwards
    #: under a braking command nobody meant as one. That cost eight
    #: collision-free runs out of sixty, and in every one of them the ego was
    #: struck while drifting backwards at a few centimetres a second.
    reverse: bool = False


class EgoController:
    """Base class for ego planners."""

    name = "controller"

    def forget(self, points) -> None:
        """Objects at these world points have been removed from the scene.

        Optional. A controller with a perception stack should drop whatever it
        believes is there, because no sensor can tell a deleted object from an
        occluded one and coasting is the right answer for the second.
        """

    def reset(self, scenario) -> None:
        """Called once before a run. Override to build maps, paths, caches."""

    def control(self, ego, view, corridor, t: float, dt: float) -> ControlCommand:
        raise NotImplementedError
