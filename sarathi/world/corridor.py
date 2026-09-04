"""The drivable corridor.

This is the single most important modelling decision in SARATHI. A conventional
stack represents the road as *lanes*; we represent it as a **corridor**: a
centreline plus a left/right width profile that may vary continuously along the
road, be eaten into by encroachment, and carry surface defects that cost speed
rather than block passage.

Lane markings, when present at all, are an *optional annotation* on the corridor -
never the thing the planner depends on. That is what lets the same planner drive an
unmarked village road and a marked urban arterial with no mode switch.

Frame convention: ``d`` is signed lateral offset from the centreline, positive to
the **left** of the direction of travel. India is left-hand traffic, so the ego's
preferred side of a two-way road is ``d > 0`` and oncoming traffic sits at ``d < 0``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..core.frenet import ReferencePath


@dataclass
class SurfaceDefect:
    """A pothole, speed breaker, mud patch or gravel patch.

    Deliberately *not* an obstacle. ``severity`` in [0,1] scales a traversal cost
    and a comfortable-speed limit; a planner may cross a shallow pothole slowly
    rather than swerve into oncoming traffic to avoid it. Treating every defect as
    a wall is a classic way to make an Indian-roads planner behave dangerously.
    """

    x: float
    y: float
    radius: float
    severity: float = 0.5
    kind: str = "pothole"

    def cost_at(self, pts: np.ndarray) -> np.ndarray:
        """Smooth radial cost bowl, zero outside ``radius``."""
        pts = np.atleast_2d(pts)
        r = np.linalg.norm(pts - np.array([self.x, self.y]), axis=1)
        u = np.clip(1.0 - r / max(self.radius, 1e-6), 0.0, 1.0)
        return self.severity * u * u

    def speed_limit(self) -> float:
        """Comfortable crossing speed in m/s; deep potholes demand a crawl."""
        if self.kind == "speed_breaker":
            return 4.0
        return float(np.interp(self.severity, [0.0, 1.0], [12.0, 2.0]))


@dataclass
class Corridor:
    """Drivable free space along a route."""

    reference: ReferencePath
    left_width: np.ndarray          # per reference sample, metres left of centreline
    right_width: np.ndarray         # per reference sample, metres right of centreline
    #: Traversable but unmetalled verge either side of the carriageway. Indian
    #: roads very rarely end at a wall: there is dirt, and drivers use it when a
    #: bus is coming the other way on a 4.4 m village road. Modelling the edge as
    #: hard makes those roads impassable in simulation while real traffic flows
    #: along them all day.
    shoulder_width: float = 0.9
    lane_marking_quality: float = 0.0   # 0 = none visible, 1 = crisp markings
    surface_quality: float = 0.7        # 0 = broken earth, 1 = new asphalt
    road_type: str = "urban"
    defects: list[SurfaceDefect] = field(default_factory=list)
    drive_side: int = +1                # +1 = keep left (India); -1 = keep right

    @classmethod
    def from_spec(cls, centreline: np.ndarray, width_profile: list[tuple],
                  **kwargs) -> "Corridor":
        """Build from a centreline and ``[(s, left_w, right_w), ...]`` control points.

        Widths are linearly interpolated in arc length, which is how a real road
        narrows at a culvert or widens at a junction mouth.
        """
        ref = ReferencePath(centreline)
        if not width_profile:
            raise ValueError("width_profile must have at least one control point")
        ctrl = np.asarray(width_profile, dtype=float)
        order = np.argsort(ctrl[:, 0])
        ctrl = ctrl[order]
        left = np.interp(ref.s, ctrl[:, 0], ctrl[:, 1])
        right = np.interp(ref.s, ctrl[:, 0], ctrl[:, 2])
        return cls(reference=ref, left_width=left, right_width=right, **kwargs)

    # -- queries ----------------------------------------------------------
    def bounds_at(self, s: float | np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Return ``(d_min, d_max)`` - the drivable lateral interval at ``s``."""
        ref = self.reference
        s = np.clip(s, 0.0, ref.length)
        left = np.interp(s, ref.s, self.left_width)
        right = np.interp(s, ref.s, self.right_width)
        return -right, left

    def hard_bounds_at(self, s: float | np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Carriageway plus verge - the absolute limit of drivable ground."""
        d_min, d_max = self.bounds_at(s)
        return d_min - self.shoulder_width, d_max + self.shoulder_width

    def width_at(self, s: float | np.ndarray) -> np.ndarray:
        d_min, d_max = self.bounds_at(s)
        return d_max - d_min

    def to_frenet_batch(self, points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Project many world points into corridor-Frenet coordinates at once."""
        return self.reference.to_frenet_batch(points)

    def contains(self, point: np.ndarray) -> bool:
        s, d = self.reference.to_frenet(np.asarray(point, dtype=float))
        d_min, d_max = self.bounds_at(s)
        return bool(d_min <= d <= d_max)

    def lateral_margin(self, point: np.ndarray) -> float:
        """Signed distance to the nearest corridor edge; negative means outside."""
        s, d = self.reference.to_frenet(np.asarray(point, dtype=float))
        d_min, d_max = self.bounds_at(s)
        return float(min(d - d_min, d_max - d))

    def defect_cost(self, pts: np.ndarray) -> np.ndarray:
        """Summed surface-defect cost at each of the given (N,2) points."""
        pts = np.atleast_2d(pts)
        total = np.zeros(len(pts))
        for defect in self.defects:
            total += defect.cost_at(pts)
        # A generally broken surface adds a uniform floor cost.
        return total + (1.0 - self.surface_quality) * 0.15

    def comfortable_speed(self, point: np.ndarray, base: float) -> float:
        """Speed cap at ``point`` given surface defects and overall road quality."""
        limit = base * (0.55 + 0.45 * self.surface_quality)
        p = np.asarray(point, dtype=float)
        for defect in self.defects:
            if float(defect.cost_at(p)[0]) > 0.02:
                limit = min(limit, defect.speed_limit())
        return limit

    def nominal_offset(self, s: float | np.ndarray) -> np.ndarray:
        """Ego's preferred lateral offset: centre of its own side of the road.

        On a two-way road this is the middle of the keep-left half. It is only ever
        a *preference* fed to the planner's cost - never a constraint.
        """
        d_min, d_max = self.bounds_at(s)
        if self.road_type == "one_way":
            return (d_min + d_max) / 2.0
        return (d_max / 2.0) if self.drive_side > 0 else (d_min / 2.0)

    def opposing_offset(self, s: float | np.ndarray) -> np.ndarray:
        """Preferred lateral offset for traffic travelling *against* the reference.

        The centre of the *other* half of the road - what a driver coming the
        other way keeps to. This is not the mirror image of
        :meth:`nominal_offset`: mirroring the ego's own preference names the
        ego's half, so oncoming traffic aims straight down the ego's side.
        """
        d_min, d_max = self.bounds_at(s)
        if self.road_type == "one_way":
            return (d_min + d_max) / 2.0
        return (d_min / 2.0) if self.drive_side > 0 else (d_max / 2.0)

    # -- rendering / geometry --------------------------------------------
    def edges(self) -> tuple[np.ndarray, np.ndarray]:
        """Left and right boundary polylines as (N,2) world-frame arrays."""
        ref = self.reference
        normal = ref.normal_at(ref.s)
        left = ref.points + self.left_width[:, None] * normal
        right = ref.points - self.right_width[:, None] * normal
        return left, right

    def polygon(self) -> np.ndarray:
        """Closed drivable-area polygon (left edge forward, right edge back)."""
        left, right = self.edges()
        return np.vstack([left, right[::-1]])
