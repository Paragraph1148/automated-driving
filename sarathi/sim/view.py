"""Per-tick view of the world in shared corridor-Frenet coordinates.

Every interaction in SARATHI - NLB-IDM leader selection, lateral clearance,
gap seeking, and later the risk field - is defined in ``(s, d)`` against the
corridor. Computing that once per tick and handing out slices is both faster and
far less error-prone than each policy re-projecting the world for itself.
"""
from __future__ import annotations

import numpy as np

from ..agents.nlbidm import Neighbour
from ..core.types import Agent
from ..world.corridor import Corridor


class FrenetView:
    """Vectorised Frenet projection of every agent, built fresh each tick."""

    __slots__ = ("corridor", "ids", "index", "s", "d", "s_dot", "d_dot",
                 "half_length", "half_width", "classes", "agents")

    def __init__(self, corridor: Corridor, agents: dict[int, Agent]):
        self.corridor = corridor
        self.agents = agents
        live = [a for a in agents.values() if a.active]
        n = len(live)
        self.ids = np.empty(n, dtype=int)
        self.s = np.empty(n)
        self.d = np.empty(n)
        self.s_dot = np.empty(n)
        self.d_dot = np.empty(n)
        self.half_length = np.empty(n)
        self.half_width = np.empty(n)
        self.classes = [None] * n
        self.index: dict[int, int] = {}

        ref = corridor.reference
        for i, a in enumerate(live):
            s, d, s_dot, d_dot = ref.state_to_frenet(a.state)
            self.ids[i] = a.id
            self.s[i], self.d[i] = s, d
            self.s_dot[i], self.d_dot[i] = s_dot, d_dot
            self.half_length[i] = a.params.length / 2.0
            self.half_width[i] = a.params.width / 2.0
            self.classes[i] = a.cls
            self.index[a.id] = i

    def frenet_of(self, agent_id: int) -> tuple[float, float, float, float]:
        i = self.index[agent_id]
        return (float(self.s[i]), float(self.d[i]),
                float(self.s_dot[i]), float(self.d_dot[i]))

    def neighbours_of(self, agent_id: int, s_ahead: float = 100.0,
                      s_behind: float = 40.0,
                      d_range: float = 12.0) -> list[Neighbour]:
        """All other agents within a longitudinal/lateral window of ``agent_id``."""
        i = self.index[agent_id]
        s0, d0 = self.s[i], self.d[i]
        ds = self.s - s0
        mask = (ds > -s_behind) & (ds < s_ahead) & (np.abs(self.d - d0) < d_range)
        mask[i] = False
        return [Neighbour(int(self.ids[j]), self.classes[j], float(self.s[j]),
                          float(self.d[j]), float(self.s_dot[j]),
                          float(self.d_dot[j]), float(self.half_length[j]),
                          float(self.half_width[j]))
                for j in np.flatnonzero(mask)]
