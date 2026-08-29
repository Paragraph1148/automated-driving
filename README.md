# SARATHI

**Situation-Aware Risk-Adaptive Trajectory & Hazard Intelligence**
Adaptive path planning and collision avoidance for autonomous vehicles on
unstructured Indian roads.

> **SIH 2026 · SIH26037 · MathWorks · Robotics and Drones**

---

Every production autonomous-driving stack assumes a **lane**. Indian roads do not
have lanes — they have a *negotiated, continuously deforming free space* shared by
buses, auto-rickshaws, two-wheelers filtering through 60 cm gaps, pushcarts,
pedestrians crossing wherever they like, and cattle.

SARATHI replaces the two primitives that break down here:

| Conventional stack | SARATHI |
|---|---|
| Lane centreline as the planning frame | **Drivable corridor**, derived from free space |
| Binary occupancy grid | **Class-conditioned continuous risk field** |
| One predicted trajectory per agent | **Multi-modal intent** with growing covariance |
| Obstacles are walls | Potholes are **traversable cost**, not walls |

Because it never needed lane markings, it degrades gracefully when they are faded,
absent, or simply wrong.

## Status

| Batch | Scope | State |
|---|---|---|
| B0 | Architecture, PS decode, team plan | done |
| B1 | Core sim, corridor, NLB-IDM traffic model | in progress |
| B2 | Perception, prediction, risk field | — |
| B3 | Hierarchical planner + safety supervisor | — |
| B4 | Web Mission Control | — |
| B5 | MATLAB / Simulink / Stateflow / RoadRunner | — |
| B6 | Monte-Carlo campaign, ablations, report | — |

## Quick start

```bash
python3 -m venv .venv && ./.venv/bin/pip install numpy scipy pyyaml pytest
./.venv/bin/python -m pytest tests/ -q
```

## Layout

```
sarathi/       Python engine (runs anywhere, no licence required)
  core/        types & Indian road-user taxonomy, geometry, Frenet, kinematics
  world/       drivable corridor, surface defects, scenario loader
  agents/      NLB-IDM + the Indian behaviour zoo
  perception/  sensor models, fusion, tracking
  prediction/  multi-modal intent prediction
  planning/    risk field, Hybrid A*, behaviour FSM, Frenet lattice, MPC
  safety/      RSS-India + control-barrier-function supervisor
  metrics/     PS metrics + Monte-Carlo campaign runner
scenarios/     *.yaml — one spec, consumed by both the Python and MATLAB runtimes
matlab/        MATLAB / Simulink / Stateflow / RoadRunner deliverable
viz/           web Mission Control
docs/          architecture, problem statement, novelty, team plan
```

Read **[docs/00-architecture.md](docs/00-architecture.md)** first — it is the plan
of record. **[docs/03-team-plan.md](docs/03-team-plan.md)** has the six-person
work split and the day-1 licence task.
