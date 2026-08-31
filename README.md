# SARATHI

**Situation-Aware Risk-Adaptive Trajectory & Hazard Intelligence**
Adaptive path planning and collision avoidance for autonomous vehicles on
unstructured Indian roads.

> **SIH 2026 · SIH26037 · MathWorks · Robotics and Drones**

---

## Run it

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) once:

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
# Windows (PowerShell)
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Then, from a fresh clone — no virtualenv, no pip install, no Python setup:

```bash
uv run sarathi serve
```

Open **http://localhost:8420**. uv creates the environment and fetches
dependencies on the first run; subsequent starts are instant.

### The live demo

Click the map to drop a road user in front of the vehicle. Shift-click removes
one. The scenario dropdown switches roads without restarting.

Nothing is scripted. The planner has no more foreknowledge of a hand-placed cow
than of anything else in the scene, and the code answering the click is the code
in the submission — which is the point. A recorded video invites the question
"how do we know it isn't a replay?"; a judge who places the obstacle himself has
already answered it.

### Everything else

```bash
uv run sarathi list                          # the ten scenarios
uv run sarathi run village_road_unmarked     # one headless run
uv run sarathi run all --chaos 0.7           # every scenario, harder
uv run sarathi run all --controller baseline # the lane-following comparison
uv run sarathi run market_dense_mixed --record run.json
uv run sarathi replay run.json -o page.html  # shareable replay page
uv run --extra dev pytest                    # 88 tests
```

`--chaos 0..1` is a single knob over the whole scene: wrong-way riders, driver
aggression, cattle, barricades and lane-marking visibility all scale with it.

### Reproducing every number we quote

```bash
uv run python scripts/benchmark.py --seeds 3      # -> artifacts/benchmark.json
uv run python scripts/capture.py --scenario bus_stop_overtake \
    --behaviour OVERTAKE --min-clearance 0.15 -o artifacts/fig-console.png
```

`benchmark.py` runs every scenario under both controllers across seeds, on
identical seeds and identical sensor noise, and writes one JSON with a row per
run. Both presentation decks in `ppt/` read that file and fail to build without
it, so a figure on a slide cannot drift away from what the code does. A run that
ends in contact records our own speed and the bearing of the other body, which
separates a collision we drove into from one where we were stationary and were
struck.

`capture.py` starts the real server, drives a real browser, and waits until the
telemetry satisfies a condition you give it before it takes the screenshot.

---

## The idea

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
| Obstacles are walls | Potholes are **traversable cost**; verges are drivable |

Because it never needed lane markings, it degrades gracefully when they are
faded, absent, or simply wrong.

## Status

| Batch | Scope | State |
|---|---|---|
| B0 | Architecture, PS decode, team plan | done |
| B1 | Core sim, corridor, NLB-IDM traffic model | done |
| B2 | Perception, prediction, risk field | done |
| B3 | Hierarchical planner + safety supervisor | works; still too conservative |
| B4 | Mission Control, live interactive demo | done |
| B5 | MATLAB / Simulink / Stateflow / RoadRunner | next |
| B6 | Monte-Carlo campaign, ablations, report | next |

Ten scenarios: the five the problem statement names, plus a single-track
causeway, a school at closing time, an unlit highway at half sensor visibility,
unmarked roadworks, and an informal bus stop.

## Layout

```
sarathi/       the stack (runs anywhere, no licence required)
  core/        types & Indian road-user taxonomy, geometry, Frenet, kinematics
  world/       drivable corridor, surface defects, scenario loader
  agents/      NLB-IDM + the Indian behaviour zoo
  perception/  sensor models, fusion, tracking
  prediction/  multi-modal intent prediction
  planning/    risk field, corridor path, Frenet lattice, behaviour FSM
  safety/      RSS-India + control-barrier supervisor
  metrics/     PS metrics + campaign runner
  assets/      the Mission Control viewer
  serve.py     live interactive server
scenarios/     *.yaml — one spec, consumed by both the Python and MATLAB runtimes
matlab/        MATLAB / Simulink / Stateflow / RoadRunner deliverable
docs/          architecture, problem statement, team plan, MATLAB bridge
```

Read **[docs/00-architecture.md](docs/00-architecture.md)** first — it is the plan
of record. **[docs/03-team-plan.md](docs/03-team-plan.md)** has the six-person work
split; **[docs/04-matlab-bridge.md](docs/04-matlab-bridge.md)** covers the licence
situation and the two-tier MATLAB design.
