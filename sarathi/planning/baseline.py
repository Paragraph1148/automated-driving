"""Baseline: a conventional lane-following planner.

This is what a competent team would build if they treated an Indian road as a
Western one - track the nominal lane offset, use IDM against the leader in your
lane, brake for anything directly ahead. It is not a straw man: on a marked road
with disciplined traffic it performs perfectly well.

It exists so the report can show, on identical seeds, *where* that approach breaks
down: it has no notion of lateral negotiation, so it cannot nudge past an
encroaching pushcart; it selects a leader by lateral proximity alone, so a filtering
two-wheeler makes it brake unnecessarily; and it has no way to reason about a cow
except as a wall.
"""
from __future__ import annotations

import math

import numpy as np

from ..agents.nlbidm import idm_interaction
from ..core.kinematics import max_steer_for, wheelbase_for
from ..core.types import AgentClass, params_for
from .base import ControlCommand, EgoController

LANE_HALF_WIDTH = 1.6      # how wide a "lane" this planner believes it occupies


class BaselineLaneFollower(EgoController):
    """Pure-pursuit lateral control + IDM longitudinal control on a fixed offset."""

    name = "baseline_lane_follower"

    def __init__(self, cruise_speed: float = 11.0, lookahead_gain: float = 0.9,
                 min_lookahead: float = 5.0):
        self.cruise_speed = cruise_speed
        self.lookahead_gain = lookahead_gain
        self.min_lookahead = min_lookahead
        self.params = params_for(AgentClass.CAR)

    def control(self, ego, view, corridor, t, dt):
        s, d, s_dot, _ = view.frenet_of(ego.id)
        ref = corridor.reference

        # Lateral: track the nominal offset with pure pursuit.
        d_target = float(corridor.nominal_offset(s))
        lookahead = max(self.min_lookahead, self.lookahead_gain * abs(s_dot))
        target = ref.to_cartesian(min(s + lookahead, ref.length), d_target)
        steer = _pure_pursuit(ego.state, np.asarray(target),
                              wheelbase_for(self.params), lookahead,
                              max_steer_for(AgentClass.CAR))

        # Longitudinal: IDM against the closest agent inside a fixed lane width.
        worst = 0.0
        governing = None
        for nb in view.neighbours_of(ego.id, s_ahead=80.0, s_behind=2.0):
            if nb.s <= s:
                continue
            if abs(nb.d - d_target) > LANE_HALF_WIDTH + nb.half_width:
                continue          # "not in my lane", so it does not exist
            gap = nb.s - s - self.params.length / 2.0 - nb.half_length
            term = idm_interaction(max(s_dot, 0.0), gap, s_dot - nb.s_dot,
                                   self.params, 1.0)
            if term > worst:
                worst, governing = term, nb.id

        v0 = min(self.cruise_speed,
                 corridor.comfortable_speed(ego.state.position, self.cruise_speed))
        free = 1.0 - (max(s_dot, 0.0) / max(v0, 0.5)) ** 4.0
        accel = self.params.a_max * (free - worst)
        accel = float(np.clip(accel, -self.params.b_max, self.params.a_max))

        return ControlCommand(accel, steer,
                              {"planner": self.name, "governing": governing,
                               "d_target": d_target})


def _pure_pursuit(state, target: np.ndarray, wheelbase: float,
                  lookahead: float, max_steer: float) -> float:
    alpha = math.atan2(target[1] - state.y, target[0] - state.x) - state.heading
    alpha = (alpha + math.pi) % (2 * math.pi) - math.pi
    steer = math.atan2(2.0 * wheelbase * math.sin(alpha), max(lookahead, 1e-3))
    return float(np.clip(steer, -max_steer, max_steer))
