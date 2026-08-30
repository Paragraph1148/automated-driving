"""Recording of simulation runs.

One JSON file per run, consumed by the web Mission Control viewer and by the
metrics layer. Keeping the recorder separate from the simulator means a headless
Monte-Carlo campaign can record nothing at all and run at full speed.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..core.types import Agent
from ..world.corridor import Corridor


@dataclass
class Frame:
    t: float
    agents: list[dict]
    ego: dict
    debug: dict = field(default_factory=dict)
    #: Planner internals for Mission Control: risk grid, prediction cones,
    #: candidate fan, chosen plan. Empty for headless campaign runs.
    layers: dict = field(default_factory=dict)


class Recorder:
    """Accumulates frames plus the static scene description."""

    def __init__(self, corridor: Corridor, scenario_name: str,
                 stride: int = 1, enabled: bool = True):
        self.enabled = enabled
        self.stride = max(1, stride)
        self.scenario_name = scenario_name
        self.frames: list[Frame] = []
        self._tick = 0
        self.scene = self._describe_scene(corridor) if enabled else {}

    @staticmethod
    def _describe_scene(corridor: Corridor) -> dict:
        left, right = corridor.edges()
        # Decimate boundaries; the viewer does not need 0.5 m resolution.
        step = max(1, len(left) // 400)
        return {
            "centreline": corridor.reference.points[::step].round(3).tolist(),
            "left_edge": left[::step].round(3).tolist(),
            "right_edge": right[::step].round(3).tolist(),
            "length": round(corridor.reference.length, 3),
            "road_type": corridor.road_type,
            "lane_marking_quality": corridor.lane_marking_quality,
            "surface_quality": corridor.surface_quality,
            "defects": [{"x": round(f.x, 2), "y": round(f.y, 2),
                         "r": round(f.radius, 2), "severity": round(f.severity, 2),
                         "kind": f.kind} for f in corridor.defects],
        }

    def capture(self, t: float, agents: dict[int, Agent], ego_id: int,
                debug: dict | None = None, layers: dict | None = None) -> None:
        if not self.enabled:
            return
        self._tick += 1
        if (self._tick - 1) % self.stride:
            return
        rows = []
        ego_row = {}
        for a in agents.values():
            if not a.active:
                continue
            row = {
                "id": a.id,
                "cls": a.cls.value,
                "x": round(a.state.x, 3),
                "y": round(a.state.y, 3),
                "h": round(a.state.heading, 4),
                "v": round(a.state.speed, 3),
                "l": round(a.params.length, 2),
                "w": round(a.params.width, 2),
            }
            if a.id == ego_id:
                ego_row = row
            else:
                rows.append(row)
        frame = Frame(round(t, 3), rows, ego_row, debug or {})
        frame.layers = layers or {}
        self.frames.append(frame)

    def to_dict(self) -> dict:
        return {
            "scenario": self.scenario_name,
            "scene": self.scene,
            "frames": [{"t": f.t, "ego": f.ego, "agents": f.agents,
                        "debug": f.debug, **f.layers} for f in self.frames],
        }

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), separators=(",", ":")))
        return path
