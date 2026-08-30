"""Live-tunable parameters.

Every threshold that shapes the vehicle's behaviour is registered here with a
range, so it can be changed from the browser while the simulation runs.

This is not a debugging convenience. It is the strongest thing we can hand a
judge: instead of asserting that the planner is conservative because of a
deliberate safety margin, we let them drag the margin and watch the behaviour
change. The ablation toggles do the same for the architecture - switch the risk
field off and the vehicle immediately drives worse, in front of them, which is a
claim no slide can make as convincingly.

Each entry names the object that owns it, so applying a change is a `setattr` on
live configuration rather than a rebuild.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Tunable:
    key: str            # "<group>.<attribute>"
    label: str
    section: str
    kind: str           # "float" or "bool"
    lo: float = 0.0
    hi: float = 1.0
    step: float = 0.01
    help: str = ""


#: Which object on the controller owns each group of parameters.
_GROUPS = {
    "drive": lambda c: c.behaviour.cfg,
    "plan": lambda c: c.lattice.cfg,
    "risk": lambda c: c.risk_cfg,
    "rss": lambda c: c.safety.p,
    "path": lambda c: c.corridor_cfg,
    "ablate": lambda c: c.cfg,
}

TUNABLES: list[Tunable] = [
    # -- how it drives ----------------------------------------------------
    Tunable("drive.desired_speed", "Desired speed", "Driving", "float",
            2.0, 22.0, 0.5, "Free-road cruise target, m/s"),
    Tunable("drive.follow_headway", "Follow headway", "Driving", "float",
            0.6, 5.0, 0.1, "Time gap below which we are following, not cruising"),
    Tunable("drive.follow_min_gap", "Leader concerns us within", "Driving", "float",
            2.0, 40.0, 1.0, "Distance at which a leader matters whatever our speed"),
    Tunable("drive.overtake_ratio", "Overtake if leader below", "Driving", "float",
            0.2, 1.0, 0.02, "Fraction of our speed at which a leader is worth passing"),
    Tunable("drive.overtake_min_width", "Overtake needs width", "Driving", "float",
            3.0, 9.0, 0.1, "Carriageway width required before passing is considered"),
    Tunable("drive.nudge_clearance", "Nudge below clearance", "Driving", "float",
            0.0, 2.5, 0.05, "Free lateral space that counts as squeezing past"),
    Tunable("drive.creep_density", "Creep above density", "Driving", "float",
            5.0, 60.0, 1.0, "Road users per 100 m that triggers walking pace"),
    Tunable("drive.dwell", "State dwell time", "Driving", "float",
            0.0, 3.0, 0.1, "Minimum time in a behaviour before switching"),

    # -- how cautious it is ----------------------------------------------
    Tunable("drive.emergency_ttc", "Emergency stop TTC", "Caution", "float",
            0.4, 5.0, 0.1, "Time-to-collision that triggers a full stop"),
    Tunable("drive.vru_time_gap", "Yield to VRU within", "Caution", "float",
            0.0, 5.0, 0.1, "Seconds to a vulnerable road user before yielding"),
    Tunable("drive.vru_min_gap", "Yield to VRU closer than", "Caution", "float",
            0.0, 6.0, 0.1, "Absolute distance that always triggers a yield"),
    Tunable("drive.wrong_way_range", "Evade wrong-way within", "Caution", "float",
            10.0, 90.0, 1.0, "Head-on detection range that triggers evasion"),
    Tunable("risk.ego_margin", "Clearance margin", "Caution", "float",
            0.0, 1.5, 0.05, "Comfort space kept around every obstacle"),
    Tunable("plan.hard_constraint_probability", "Treat prediction as certain above",
            "Caution", "float", 0.05, 1.0, 0.05,
            "Manoeuvre probability that becomes a hard obstacle rather than a cost"),

    # -- the trajectory search -------------------------------------------
    Tunable("plan.lateral_span", "Lateral search span", "Planner", "float",
            0.5, 5.0, 0.1, "How far either side of the reference we sample"),
    Tunable("plan.max_lateral_rate", "Lateral agility", "Planner", "float",
            0.05, 0.8, 0.01, "Sideways metres per metre travelled forward"),
    Tunable("plan.a_long_max", "Max acceleration", "Planner", "float",
            0.5, 4.0, 0.1, "Comfortable longitudinal acceleration, m/s²"),
    Tunable("plan.curvature_max", "Max curvature", "Planner", "float",
            0.05, 0.6, 0.01, "Tightest turn the planner will propose, 1/m"),
    Tunable("plan.w_risk", "Weight: risk", "Planner", "float",
            0.0, 40.0, 0.5, "How strongly risk dominates trajectory choice"),
    Tunable("plan.w_speed", "Weight: progress", "Planner", "float",
            0.0, 12.0, 0.2, "How strongly falling short of target speed is penalised"),
    Tunable("plan.w_offset", "Weight: stay centred", "Planner", "float",
            0.0, 6.0, 0.1, "Preference for holding the reference line"),
    Tunable("path.blockage_weight", "Route around obstructions", "Planner", "float",
            0.0, 150.0, 5.0, "How hard the reference path avoids stopped objects"),

    # -- the safety floor -------------------------------------------------
    Tunable("rss.rho", "Reaction time", "Safety", "float",
            0.1, 2.0, 0.05, "Assumed delay before braking begins, s"),
    Tunable("rss.b_min", "Guaranteed braking", "Safety", "float",
            1.5, 8.0, 0.1, "Deceleration the supervisor assumes we can achieve"),
    Tunable("rss.mu_lateral", "Lateral safety margin", "Safety", "float",
            0.0, 1.2, 0.05, "Sideways gap RSS insists on"),
    Tunable("rss.alpha", "Barrier gain", "Safety", "float",
            0.2, 5.0, 0.1, "How sharply the speed cap tightens as margin closes"),
    Tunable("rss.min_standoff", "Standoff from stopped", "Safety", "float",
            0.0, 8.0, 0.25, "Never creep closer than this behind a stopped vehicle"),

    # -- architecture ablations ------------------------------------------
    Tunable("ablate.use_risk_field", "Risk field", "Ablations", "bool",
            help="Off: uniform occupancy instead of class-conditioned risk"),
    Tunable("ablate.use_multimodal_prediction", "Multi-modal prediction",
            "Ablations", "bool",
            help="Off: only the single most likely future per agent"),
    Tunable("ablate.use_safety_supervisor", "RSS supervisor", "Ablations", "bool",
            help="Off: the planner's command is not filtered"),
    Tunable("ablate.use_derived_reference", "Corridor-derived reference",
            "Ablations", "bool",
            help="Off: the reference ignores obstructions"),
]

_BY_KEY = {t.key: t for t in TUNABLES}


def _target(controller, key: str):
    group, attr = key.split(".", 1)
    if group not in _GROUPS:
        raise KeyError(f"unknown tuning group {group!r}")
    return _GROUPS[group](controller), attr


def read_all(controller) -> dict[str, float | bool]:
    """Current value of every registered parameter."""
    out: dict[str, float | bool] = {}
    for tunable in TUNABLES:
        obj, attr = _target(controller, tunable.key)
        value = getattr(obj, attr, None)
        if value is None:
            continue
        out[tunable.key] = bool(value) if tunable.kind == "bool" else float(value)
    return out


def apply(controller, key: str, value) -> float | bool:
    """Set one parameter, clamped to its declared range. Returns what was set."""
    tunable = _BY_KEY.get(key)
    if tunable is None:
        raise KeyError(f"unknown tunable {key!r}")
    obj, attr = _target(controller, key)
    if tunable.kind == "bool":
        coerced: float | bool = bool(value)
    else:
        coerced = float(min(max(float(value), tunable.lo), tunable.hi))
    setattr(obj, attr, coerced)
    return coerced


def schema() -> list[dict]:
    """Serialisable description of the panel, for the browser to build itself."""
    return [{"key": t.key, "label": t.label, "section": t.section, "kind": t.kind,
             "lo": t.lo, "hi": t.hi, "step": t.step, "help": t.help}
            for t in TUNABLES]
