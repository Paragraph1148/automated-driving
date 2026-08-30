"""Visual snapshot of the planner's internal state, for Mission Control.

Everything here exists to be *drawn*. The point of the view is that a judge can
see what the planner is thinking - the risk field it is avoiding, the futures it
is hedging against, the fan of trajectories it considered and the one it chose -
rather than watching a rectangle move and taking our word for it.

Payload size is the binding constraint, so the risk field ships as a quantised
byte grid in ego-local coordinates and everything else is decimated and rounded.
"""
from __future__ import annotations

import base64

import numpy as np

#: Ego-local risk grid: metres ahead, behind, and to each side, and cell size.
GRID_AHEAD = 60.0
GRID_BEHIND = 12.0
GRID_SIDE = 16.0
GRID_RES = 1.5
#: Candidate trajectories drawn in the fan, and points per trajectory.
FAN_CANDIDATES = 26
FAN_POINTS = 7
#: Tracks whose predictions are drawn, and points per predicted mode.
CONE_TRACKS = 10
CONE_POINTS = 5


def planner_snapshot(controller, ego) -> dict:
    """Collect the drawable state of one planning cycle."""
    out: dict = {}
    plan = getattr(controller, "plan", None)
    if plan is not None:
        out["plan"] = _round(plan.xy[::2])
        out["plan_v"] = [round(float(v), 2) for v in plan.speed[::2]]

    out["fan"] = _fan(getattr(controller, "candidates", []))
    out["cones"] = _cones(getattr(controller, "predictions", []), ego)
    out["tracks"] = _tracks(getattr(controller, "tracks", []))
    reference = getattr(controller, "reference", None)
    if reference is not None:
        out["reference"] = _round(reference.points[::8])
    grid = _risk_grid(getattr(controller, "risk_field", None), ego)
    if grid is not None:
        out["risk"] = grid
    return out


def _round(points: np.ndarray, places: int = 2) -> list:
    return np.asarray(points, dtype=float).round(places).tolist()


def _fan(candidates: list) -> list:
    """A spread of considered trajectories, feasible and not."""
    if not candidates:
        return []
    live = [c for c in candidates if c.feasible]
    dead = [c for c in candidates if not c.feasible]
    live.sort(key=lambda c: c.cost)
    # Show the best few in full, then sample across the rest so the fan reads as
    # a fan rather than a bundle of near-identical best options.
    chosen = live[:6]
    if len(live) > 6:
        step = max(1, len(live) // 12)
        chosen += live[6::step][:12]
    if dead:
        step = max(1, len(dead) // 8)
        chosen += dead[::step][:8]

    out = []
    for c in chosen[:FAN_CANDIDATES]:
        stride = max(1, len(c.xy) // FAN_POINTS)
        out.append({
            "p": _round(c.xy[::stride], 1),
            "ok": bool(c.feasible),
            "c": round(float(c.cost), 2) if np.isfinite(c.cost) else None,
        })
    return out


def _cones(predictions: list, ego) -> list:
    """Predicted futures, as position plus lateral spread at each step."""
    if not predictions:
        return []
    ego_p = ego.state.position
    ranked = sorted(
        predictions,
        key=lambda p: float(np.linalg.norm(p.modes[0].positions[0] - ego_p))
        if p.modes else 1e9)
    out = []
    for pred in ranked[:CONE_TRACKS]:
        modes = sorted(pred.modes, key=lambda m: -m.probability)[:2]
        rows = []
        for mode in modes:
            stride = max(1, len(mode.positions) // CONE_POINTS)
            rows.append({
                "m": mode.manoeuvre.value,
                "p": round(float(mode.probability), 2),
                "xy": _round(mode.positions[::stride], 1),
                "s": [round(float(v), 2) for v in mode.sigma_lat[::stride]],
            })
        out.append({"id": int(pred.track_id), "cls": pred.cls.value, "modes": rows})
    return out


def _tracks(tracks: list) -> list:
    """What perception believes exists - deliberately not the ground truth."""
    return [{
        "id": int(t.id), "cls": t.cls.value,
        "x": round(float(t.x), 2), "y": round(float(t.y), 2),
        "h": round(float(t.heading), 3), "v": round(float(t.speed), 2),
        "conf": round(float(t.cls_confidence), 2),
        "sig": round(float(t.position_sigma), 2),
    } for t in tracks]


def _risk_grid(field, ego) -> dict | None:
    """Quantised ego-local risk field, base64 encoded."""
    if field is None:
        return None
    nx = int((GRID_AHEAD + GRID_BEHIND) / GRID_RES)
    ny = int((2 * GRID_SIDE) / GRID_RES)
    xs = np.linspace(-GRID_BEHIND, GRID_AHEAD, nx)
    ys = np.linspace(-GRID_SIDE, GRID_SIDE, ny)
    gx, gy = np.meshgrid(xs, ys)

    c, s = np.cos(ego.state.heading), np.sin(ego.state.heading)
    world = np.column_stack([
        ego.state.x + gx.ravel() * c - gy.ravel() * s,
        ego.state.y + gx.ravel() * s + gy.ravel() * c,
    ])
    # Agents and surface defects only - deliberately not the corridor-boundary
    # term. That term saturates everywhere off the road, so including it renders
    # as a solid block that hides the very thing the layer exists to show. The
    # carriageway polygon already communicates where the road ends.
    values = field.agent_risk(world, 0.0)
    values = values + field.cfg.defect_weight * field.corridor.defect_cost(world)
    cap = max(float(np.percentile(values, 99.5)), 0.6)
    bytes_ = np.clip(values / cap * 255.0, 0, 255).astype(np.uint8)
    return {
        "nx": nx, "ny": ny, "res": GRID_RES,
        "x0": -GRID_BEHIND, "y0": -GRID_SIDE,
        "data": base64.b64encode(bytes_.tobytes()).decode("ascii"),
    }
