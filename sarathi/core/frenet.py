"""Frenet reference frames.

The reference path here is deliberately *not* a lane centreline. On an unstructured
road we synthesise it from the drivable corridor (see ``sarathi.world.corridor``),
which is why the planner keeps working when lane markings are absent, faded, or
simply wrong. Everything downstream only needs *some* smooth reference curve.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.spatial import cKDTree

from .geom import (polyline_arclength, polyline_curvature, polyline_tangents,
                   project_point_to_polyline, resample_polyline)
from .types import State, wrap_to_pi


class ReferencePath:
    """A smooth curve supporting global <-> Frenet conversion.

    Frenet convention: ``s`` is arc length from the path start, ``d`` is signed
    lateral offset, positive to the **left** of the direction of travel.
    """

    def __init__(self, points: np.ndarray, spacing: float = 0.5,
                 smooth_window: int = 9):
        pts = np.asarray(points, dtype=float)
        if pts.ndim != 2 or pts.shape[1] != 2 or len(pts) < 2:
            raise ValueError("reference path needs an (N,2) array with N >= 2")
        pts = resample_polyline(pts, spacing)
        if smooth_window > 2 and len(pts) > smooth_window:
            pts = _smooth_polyline(pts, smooth_window)
        self.points = pts
        self.s = polyline_arclength(pts)
        self.tangents = polyline_tangents(pts)
        self.headings = np.unwrap(np.arctan2(self.tangents[:, 1],
                                             self.tangents[:, 0]))
        self.curvature = polyline_curvature(pts, self.s)
        self.length = float(self.s[-1])

        # Segment geometry and a vertex KD-tree, both precomputed once. Batched
        # projection is the planner's hottest path: scoring a fan of candidate
        # trajectories projects a few thousand points per tick, and scanning every
        # segment for every point cost ~70 ms per call.
        self._seg_a = pts[:-1]
        self._seg_ab = pts[1:] - pts[:-1]
        self._seg_len_sq = np.maximum(
            np.einsum("ij,ij->i", self._seg_ab, self._seg_ab), 1e-12)
        self._seg_len = np.sqrt(self._seg_len_sq)
        tangent = self._seg_ab / self._seg_len[:, None]
        self._seg_normal = np.column_stack([-tangent[:, 1], tangent[:, 0]])
        self._tree = cKDTree(pts)

    # -- sampling ---------------------------------------------------------
    def point_at(self, s: float | np.ndarray) -> np.ndarray:
        s = np.clip(s, 0.0, self.length)
        return np.stack([np.interp(s, self.s, self.points[:, 0]),
                         np.interp(s, self.s, self.points[:, 1])], axis=-1)

    def heading_at(self, s: float | np.ndarray) -> float | np.ndarray:
        s = np.clip(s, 0.0, self.length)
        return wrap_to_pi(np.interp(s, self.s, self.headings))

    def curvature_at(self, s: float | np.ndarray) -> float | np.ndarray:
        s = np.clip(s, 0.0, self.length)
        return np.interp(s, self.s, self.curvature)

    def normal_at(self, s: float | np.ndarray) -> np.ndarray:
        th = self.heading_at(s)
        return np.stack([-np.sin(th), np.cos(th)], axis=-1)

    # -- conversion -------------------------------------------------------
    def to_frenet(self, point: np.ndarray) -> tuple[float, float]:
        s, d, _ = project_point_to_polyline(self.points, self.s,
                                            np.asarray(point, dtype=float))
        return s, d

    def to_frenet_batch(self, queries: np.ndarray
                        ) -> tuple[np.ndarray, np.ndarray]:
        """Project many world points into Frenet coordinates at once.

        Finds the nearest polyline *vertex* with a KD-tree, then solves exactly on
        the (at most two) segments incident to it. With the path resampled to 0.5 m
        the true closest point always lies on one of those two, so this is exact,
        not an approximation.
        """
        queries = np.atleast_2d(np.asarray(queries, dtype=float))
        n_seg = len(self._seg_a)
        _, vertex = self._tree.query(queries)
        vertex = np.asarray(vertex, dtype=int)

        left = np.clip(vertex - 1, 0, n_seg - 1)
        right = np.clip(vertex, 0, n_seg - 1)

        def solve(seg: np.ndarray):
            a = self._seg_a[seg]
            ab = self._seg_ab[seg]
            t = np.clip(np.einsum("nj,nj->n", queries - a, ab) /
                        self._seg_len_sq[seg], 0.0, 1.0)
            proj = a + t[:, None] * ab
            diff = queries - proj
            return t, np.einsum("nj,nj->n", diff, diff), diff

        t_l, d2_l, diff_l = solve(left)
        t_r, d2_r, diff_r = solve(right)
        take_left = d2_l < d2_r
        seg = np.where(take_left, left, right)
        t = np.where(take_left, t_l, t_r)
        diff = np.where(take_left[:, None], diff_l, diff_r)

        s = self.s[seg] + t * self._seg_len[seg]
        d = np.einsum("nj,nj->n", diff, self._seg_normal[seg])
        return s, d

    def to_cartesian(self, s: float | np.ndarray,
                     d: float | np.ndarray) -> np.ndarray:
        base = self.point_at(s)
        normal = self.normal_at(s)
        return base + np.asarray(d)[..., None] * normal if np.ndim(d) else base + d * normal

    def state_to_frenet(self, state: State) -> tuple[float, float, float, float]:
        """Return ``(s, d, s_dot, d_dot)`` for a world-frame state.

        ``s_dot`` is corrected by the ``1 - kappa*d`` factor, so a vehicle running
        wide on a curve reports the correct progress rate rather than its raw speed.
        """
        s, d = self.to_frenet(state.position)
        th_ref = float(self.heading_at(s))
        kappa = float(self.curvature_at(s))
        v = state.velocity
        v_long = float(v[0] * math.cos(th_ref) + v[1] * math.sin(th_ref))
        d_dot = float(-v[0] * math.sin(th_ref) + v[1] * math.cos(th_ref))
        one_minus_kd = max(1e-3, 1.0 - kappa * d)
        return s, d, v_long / one_minus_kd, d_dot

    def heading_error(self, state: State) -> float:
        s, _ = self.to_frenet(state.position)
        return float(wrap_to_pi(state.heading - float(self.heading_at(s))))

    # -- trajectory helpers ----------------------------------------------
    def frenet_traj_to_cartesian(self, s: np.ndarray, d: np.ndarray,
                                 s_dot: np.ndarray | None = None,
                                 d_dot: np.ndarray | None = None
                                 ) -> tuple[np.ndarray, np.ndarray]:
        """Lift a Frenet trajectory to world (N,2) positions and headings."""
        s = np.asarray(s, dtype=float)
        d = np.asarray(d, dtype=float)
        base = self.point_at(s)
        th_ref = self.heading_at(s)
        normal = np.stack([-np.sin(th_ref), np.cos(th_ref)], axis=-1)
        xy = base + d[:, None] * normal
        if s_dot is None or d_dot is None:
            # Fall back to finite differences of the lifted path.
            dxy = np.gradient(xy, axis=0)
            heading = np.arctan2(dxy[:, 1], dxy[:, 0])
        else:
            kappa = self.curvature_at(s)
            one_minus_kd = np.maximum(1e-3, 1.0 - kappa * d)
            heading = th_ref + np.arctan2(np.asarray(d_dot),
                                          np.maximum(1e-3, np.asarray(s_dot)) * one_minus_kd)
        return xy, wrap_to_pi(heading)


def _smooth_polyline(pts: np.ndarray, window: int) -> np.ndarray:
    """Moving-average smoothing with clamped endpoints.

    Endpoints are pinned so a corridor centreline keeps its start and goal exactly.
    """
    if window % 2 == 0:
        window += 1
    half = window // 2
    padded = np.vstack([np.repeat(pts[:1], half, axis=0),
                        pts,
                        np.repeat(pts[-1:], half, axis=0)])
    kernel = np.ones(window) / window
    out = np.column_stack([np.convolve(padded[:, 0], kernel, mode="valid"),
                           np.convolve(padded[:, 1], kernel, mode="valid")])
    out[0] = pts[0]
    out[-1] = pts[-1]
    return out
