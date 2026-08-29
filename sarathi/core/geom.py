"""Planar geometry helpers: polylines, projections, and convex collision tests."""
from __future__ import annotations

import numpy as np


def polyline_arclength(pts: np.ndarray) -> np.ndarray:
    """Cumulative arc length along an (N,2) polyline, starting at 0."""
    seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    return np.concatenate([[0.0], np.cumsum(seg)])


def resample_polyline(pts: np.ndarray, spacing: float) -> np.ndarray:
    """Resample an (N,2) polyline to approximately uniform ``spacing``."""
    pts = np.asarray(pts, dtype=float)
    s = polyline_arclength(pts)
    total = float(s[-1])
    if total <= 0.0:
        return pts.copy()
    n = max(2, int(round(total / spacing)) + 1)
    s_new = np.linspace(0.0, total, n)
    return np.column_stack([np.interp(s_new, s, pts[:, 0]),
                            np.interp(s_new, s, pts[:, 1])])


def polyline_tangents(pts: np.ndarray) -> np.ndarray:
    """Unit tangent at each vertex, using central differences."""
    d = np.gradient(pts, axis=0)
    norms = np.linalg.norm(d, axis=1, keepdims=True)
    norms[norms < 1e-12] = 1.0
    return d / norms


def polyline_curvature(pts: np.ndarray, s: np.ndarray) -> np.ndarray:
    """Signed curvature kappa = (x' y'' - y' x'') / (x'^2 + y'^2)^{3/2}."""
    # Guard against duplicate arc-length samples, which make gradients blow up.
    ds = np.gradient(s)
    ds[np.abs(ds) < 1e-9] = 1e-9
    dx = np.gradient(pts[:, 0]) / ds
    dy = np.gradient(pts[:, 1]) / ds
    ddx = np.gradient(dx) / ds
    ddy = np.gradient(dy) / ds
    denom = (dx * dx + dy * dy) ** 1.5
    denom[denom < 1e-9] = 1e-9
    return (dx * ddy - dy * ddx) / denom


def project_point_to_polyline(pts: np.ndarray, s: np.ndarray,
                              p: np.ndarray) -> tuple[float, float, int]:
    """Project ``p`` onto the polyline.

    Returns ``(s_proj, d_signed, seg_index)`` where ``d_signed`` is positive to the
    left of the direction of travel.
    """
    a = pts[:-1]
    b = pts[1:]
    ab = b - a
    ap = p[None, :] - a
    denom = np.einsum("ij,ij->i", ab, ab)
    denom[denom < 1e-12] = 1e-12
    t = np.clip(np.einsum("ij,ij->i", ap, ab) / denom, 0.0, 1.0)
    proj = a + t[:, None] * ab
    dist = np.linalg.norm(p[None, :] - proj, axis=1)
    i = int(np.argmin(dist))

    seg_len = np.sqrt(denom[i])
    s_proj = float(s[i] + t[i] * seg_len)
    tangent = ab[i] / seg_len
    normal = np.array([-tangent[1], tangent[0]])   # left-hand normal
    d_signed = float(np.dot(p - proj[i], normal))
    return s_proj, d_signed, i


def _project_polygon(poly: np.ndarray, axis: np.ndarray) -> tuple[float, float]:
    proj = poly @ axis
    return float(proj.min()), float(proj.max())


def convex_polygons_intersect(a: np.ndarray, b: np.ndarray) -> bool:
    """Separating-axis test for two convex polygons given as (N,2) vertex arrays."""
    for poly in (a, b):
        n = len(poly)
        for i in range(n):
            edge = poly[(i + 1) % n] - poly[i]
            axis = np.array([-edge[1], edge[0]])
            norm = np.linalg.norm(axis)
            if norm < 1e-12:
                continue
            axis = axis / norm
            amin, amax = _project_polygon(a, axis)
            bmin, bmax = _project_polygon(b, axis)
            if amax < bmin or bmax < amin:
                return False   # found a separating axis
    return True


def polygon_distance(a: np.ndarray, b: np.ndarray) -> float:
    """Minimum distance between two convex polygons; 0.0 if they overlap."""
    if convex_polygons_intersect(a, b):
        return 0.0
    return min(_polygon_to_segments_distance(a, b),
               _polygon_to_segments_distance(b, a))


def _polygon_to_segments_distance(a: np.ndarray, b: np.ndarray) -> float:
    best = float("inf")
    n = len(b)
    for i in range(n):
        p, q = b[i], b[(i + 1) % n]
        best = min(best, float(np.min(point_segment_distance(a, p, q))))
    return best


def point_segment_distance(pts: np.ndarray, a: np.ndarray,
                           b: np.ndarray) -> np.ndarray:
    """Distance from each row of ``pts`` (N,2) to segment ``a``-``b``."""
    pts = np.atleast_2d(pts)
    ab = b - a
    denom = float(ab @ ab)
    if denom < 1e-12:
        return np.linalg.norm(pts - a[None, :], axis=1)
    t = np.clip((pts - a[None, :]) @ ab / denom, 0.0, 1.0)
    proj = a[None, :] + t[:, None] * ab[None, :]
    return np.linalg.norm(pts - proj, axis=1)


def point_in_polygon(p: np.ndarray, poly: np.ndarray) -> bool:
    """Ray-casting point-in-polygon test (works for any simple polygon)."""
    x, y = float(p[0]), float(p[1])
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / (yj - yi + 1e-15) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def points_in_polygon(pts: np.ndarray, poly: np.ndarray) -> np.ndarray:
    """Vectorised ray-casting test for many points against one polygon."""
    pts = np.atleast_2d(pts)
    x, y = pts[:, 0], pts[:, 1]
    inside = np.zeros(len(pts), dtype=bool)
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        straddles = (yi > y) != (yj > y)
        with np.errstate(divide="ignore", invalid="ignore"):
            x_cross = (xj - xi) * (y - yi) / (yj - yi + 1e-15) + xi
        inside ^= straddles & (x < x_cross)
        j = i
    return inside
