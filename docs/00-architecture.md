# SARATHI — Architecture & Master Plan

**SARATHI** — *Situation-Aware Risk-Adaptive Trajectory & Hazard Intelligence*
> In the Mahabharata, the *sārathi* is the charioteer who reads the chaos of the
> battlefield and steers the warrior through it. That is exactly the job here.

Target: **SIH26037** (MathWorks) — *Adaptive Path Planning and Collision Avoidance
for Autonomous Vehicles on Unstructured Indian Roads*.

---

## 1. The thesis (this is the pitch, memorise it)

> Every autonomous-driving stack in production assumes a **lane**. Indian roads do
> not have lanes — they have a *negotiated, continuously deforming free space*.
> SARATHI throws away the lane as the planning primitive and replaces it with a
> **drivable corridor + a continuous risk field**. Because it never needed lane
> markings, it degrades gracefully when they vanish, when a cow enters, when a
> two-wheeler filters through a 0.8 m gap, or when a rider comes at you the wrong way.

Three claims we must *demonstrate*, not assert:

| # | Claim | Evidence we ship |
|---|-------|------------------|
| C1 | Lane-free planning works where lane-based planning fails | A/B: SARATHI vs a baseline lane-following Frenet planner, same seeds, same scenarios. Baseline collides/stalls; we don't. |
| C2 | Indian road users need *class-specific* risk models, not one bounding box | Ablation: uniform-risk vs class-conditioned risk field. Show min-TTC and collision-rate deltas. |
| C3 | It is real-time and safe, not a cherry-picked demo | Monte-Carlo: 5 scenarios × N seeds × 4 chaos levels, with p50/p95/p99 replan latency and collision rate. |

Anyone can build a demo that survives one scripted cow. **The ablations and the
Monte-Carlo campaign are what separate us from the other 400 teams.**

---

## 2. What the problem statement actually demands

Decoded from the verbatim PS (`docs/01-problem-statement.md`):

| PS requirement | Where we satisfy it |
|---|---|
| Perception via camera + LiDAR + radar | `sarathi/perception/` — sensor models + fusion → tracks |
| Identify auto-rickshaws, pushcarts, pedestrians, animals | `sarathi/agents/taxonomy.py` — 12-class Indian taxonomy; detector trained on **IDD** |
| Predict short-term motion incl. **non-lane-based / irregular** | `sarathi/prediction/` — multi-modal intent GMM |
| Safe collision-free path, **replanned in real time** | `sarathi/planning/` — corridor + risk field + Frenet lattice |
| Missing lane markings | Corridor is derived from *drivable area*, never from markings |
| Informal merging | Behaviour FSM: `NEGOTIATE`, `NUDGE`, `YIELD` |
| Sudden pedestrian movement | Prediction `DART` mode + safety supervisor |
| Unexpected obstacles | Dynamic corridor re-derivation each tick |
| ≥5 scenarios (village / intersection / merge / market / cattle) | `scenarios/*.yaml` — exactly these five, plus stress variants |
| MATLAB + Simulink pipeline | `matlab/` — Batch 5 (see §7) |
| ≥2 RoadRunner scenes | `matlab/roadrunner/` + scene spec sheets for the non-coders |
| Metrics: replan latency, path smoothness, completion rate | `sarathi/metrics/` — plus min-TTC, PET, jerk, intervention count |
| Technical report + demo video | `docs/`, Batch 6 |

**Nothing in that table is optional.** A gap here is a direct mark deduction from a
MathWorks judge reading their own rubric.

---

## 3. System architecture

```
                          ┌──────────────────── SARATHI ────────────────────┐
  scenario.yaml ─────────►│                                                  │
  (one spec, two runtimes)│   ┌──────────┐   ┌───────────┐   ┌───────────┐  │
                          │   │ SENSORS  │──►│  FUSION   │──►│ PREDICTION│  │
  ┌───────────────┐       │   │ cam/lidar│   │ track mgmt│   │ intent GMM│  │
  │ INDIAN TRAFFIC│──────►│   │ /radar   │   │ (JPDA-ish)│   │ + covar   │  │
  │    WORLD      │ truth │   └──────────┘   └───────────┘   └─────┬─────┘  │
  │               │       │                                        │         │
  │ • NLB-IDM     │       │   ┌────────────────────────────────────▼──────┐ │
  │ • 12 classes  │       │   │   INDIAN DRIVING RISK FIELD (IDRF)        │ │
  │ • wrong-way   │       │   │   class-conditioned, anisotropic,         │ │
  │ • cattle      │       │   │   uncertainty-inflated, predicted forward │ │
  │ • potholes    │       │   └────────────────────┬──────────────────────┘ │
  │ • barricades  │       │                        │                        │
  │ • chaos knob  │       │   ┌──────────┐  ┌──────▼──────┐  ┌───────────┐ │
  └───────▲───────┘       │   │  GLOBAL  │─►│ BEHAVIOUR   │─►│   LOCAL   │ │
          │               │   │ Hybrid A*│  │ FSM (8 st.) │  │  Frenet   │ │
          │  ego cmd      │   │ on cost  │  │ Stateflow-  │  │  lattice  │ │
          └───────────────┤   │   map    │  │ equivalent  │  │  + MPC    │ │
                          │   └──────────┘  └─────────────┘  └─────┬─────┘ │
                          │                                         │       │
                          │   ┌─────────────────────────────────────▼─────┐│
                          │   │  SAFETY SUPERVISOR (RSS-India + CBF)      ││
                          │   │  provable filter — can only ever slow down││
                          │   └─────────────────────┬─────────────────────┘│
                          └─────────────────────────┼──────────────────────┘
                                                    ▼
                              ┌──────────────┐  ┌──────────────────┐
                              │ METRICS/     │  │ WEB MISSION      │
                              │ MONTE-CARLO  │  │ CONTROL (live)   │
                              └──────────────┘  └──────────────────┘
```

### 3.1 The seven modules

**M1 — Indian Traffic World** (`sarathi/world`, `sarathi/agents`)
The part nobody else will build. A microscopic traffic simulator whose agents obey
**NLB-IDM** (Non-Lane-Based Intelligent Driver Model): longitudinal IDM extended
with *minimum lateral clearance*, *dynamic pseudo-lane*, *centreline separation
ratio*, and per-class parameters. On top of it, a behaviour zoo:
`GapFillingTwoWheeler`, `WrongWayRider`, `RashDriver`, `Cattle`, `Pushcart`,
`JaywalkingPedestrian`, `AutoRickshawSuddenStop`, `ParkedEncroachment`.
A single **`chaos` scalar (0–1)** scales wrong-way probability, lane-marking
visibility, cattle spawn rate, aggression distribution and barricade density —
which is what makes the Monte-Carlo campaign and the live demo slider possible.

**M2 — Perception** (`sarathi/perception`)
Camera (FOV + range + occlusion + class-conditioned miss rate), LiDAR (ray-cast
against agent polygons, range noise), radar (range-rate, poor lateral resolution).
Fused into tracks with realistic latency, false negatives and ID switches — because
*planning against ground truth is the single most common way student demos lie.*

**M3 — Prediction** (`sarathi/prediction`)
Per-agent multi-modal intent distribution over `{CONTINUE, CUT_IN, WRONG_WAY,
STOP, DART, FILTER}` with class-conditioned priors, each mode a polynomial
trajectory with growing covariance. A cow gets near-isotropic high variance; a bus
gets a tight, near-deterministic tube. This is what the judges see as *prediction
cones* on screen.

**M4 — Indian Driving Risk Field** (`sarathi/planning/risk.py`) — **our core novelty**
Replaces binary occupancy with a continuous cost `R(x, y, t)`:
- **Anisotropic kernels** — elongated along heading, scaled by speed.
- **Class-conditioned shape** — two-wheeler: wide lateral variance (it *will* filter);
  cow: large isotropic, high uncertainty growth; wrong-way rider: forward-elongated
  and *adversarial* (assumes worst-case for us).
- **Traversable costs, not obstacles** — a pothole is a *bowl of cost* you may cross
  slowly, not a wall. A no-go for a sedan may be fine for a higher-clearance path.
- **Time-indexed** — the field is predicted forward, so planning is in space-*time*.

**M5 — Hierarchical planner** (`sarathi/planning`)
1. *Global*: Hybrid A* over a drivable-area cost map (no lane graph needed).
2. *Behaviour*: 8-state FSM — `CRUISE, FOLLOW, NUDGE, OVERTAKE, YIELD, CREEP,
   WRONG_WAY_EVADE, EMERGENCY_STOP`. Mirrors 1:1 to a **Stateflow chart** in Batch 5.
3. *Local*: **Frenet lattice on a virtual reference path** derived from the corridor
   centreline — this is the key trick that makes Frenet planning work with no lane
   markings. Sampled trajectories scored by IDRF integral + jerk + progress + offset.
4. *Control*: kinematic bicycle + MPC (fallback: pure-pursuit + PID).

**M6 — Safety supervisor** (`sarathi/safety`)
RSS-style longitudinal *and lateral* safe distances with **India-calibrated
parameters** (shorter reaction times, smaller accepted lateral gaps, two-wheeler
filtering allowance), plus a control-barrier-function filter. It is *monotone*: it
can only ever reduce speed or increase clearance. Judges love a provable layer.

**M7 — Validation** (`sarathi/metrics`)
The PS metrics (replan latency, path smoothness, scenario completion rate) plus
min-TTC, minimum clearance, post-encroachment time, jerk RMS, comfort, and
intervention count. Run as a **campaign**: 5 scenarios × N seeds × 4 chaos levels,
emitting a report with plots and a pass/fail regression gate.

---

## 4. Why this beats a normal submission

| Typical team | SARATHI |
|---|---|
| YOLO on IDD + A\* + "avoids obstacle" video | Full closed loop, sensors→fusion→prediction→plan→control→safety |
| Plans against ground truth | Plans against noisy, occluded, latent tracks |
| Binary occupancy grid | Continuous class-conditioned risk field |
| Single predicted line per agent | Multi-modal intent with covariance |
| 5 hand-run demo videos | Monte-Carlo campaign with p95 latency + collision rate |
| "It works" | Ablations proving *which idea* bought the safety |
| Cow = a box | Cow = an unpredictable, high-variance, class-specific hazard model |
| Lane-following | Lane-*free* corridor planning; lane markings are optional input |

---

## 5. Repository layout

```
sarathi/          Python reference implementation (runs anywhere, no licence)
  core/           types, geometry, Frenet frames, bicycle kinematics
  world/          drivable corridor, surface defects, scenario loader
  agents/         Indian taxonomy + NLB-IDM + behaviour zoo
  perception/     sensor models + fusion + tracking
  prediction/     multi-modal intent & trajectory prediction
  planning/       risk field, Hybrid A*, behaviour FSM, Frenet lattice, MPC
  safety/         RSS-India + CBF supervisor
  sim/            fixed-step simulator, recorder
  metrics/        PS metrics + campaign runner
scenarios/        *.yaml — one spec consumed by BOTH runtimes
matlab/           MATLAB/Simulink/Stateflow + RoadRunner (Batch 5)
viz/              web Mission Control (Batch 4)
docs/             PS decode, novelty, team plan, MATLAB bridge
tests/            pytest
```

**One scenario spec, two runtimes.** `scenarios/*.yaml` is consumed by the Python
engine *and* by the MATLAB importer. That is itself a strong engineering story —
and it de-risks the licence question completely.

---

## 6. Delivery batches

| Batch | Content | Gate |
|---|---|---|
| **B0** | Scaffold, architecture doc, PS decode, team plan | this document |
| **B1** | Core sim: geometry, Frenet, bicycle, corridor, NLB-IDM, behaviour zoo, 5 scenarios, headless runner, metrics | `pytest` green; all 5 scenarios run |
| **B2** | Perception (cam/LiDAR/radar + fusion) + multi-modal prediction + **IDRF** | risk field renders; tracks degrade realistically |
| **B3** | Planner: Hybrid A*, behaviour FSM, Frenet lattice, MPC, safety supervisor | closed loop, 0 collisions on all 5 at chaos 0.5 |
| **B4** | **Web Mission Control** — live BEV, risk heat-map, prediction cones, trajectory fan, chaos slider | the demo you show on the 31st |
| **B5** | MATLAB/Simulink port + Stateflow + RoadRunner scenes + `build_model.m` | rubric compliance |
| **B6** | Monte-Carlo campaign, ablations, A/B vs baseline, technical report, deck, video | the winning evidence |

---

## 7. The MATLAB question — settled

The PS *Expected Solution* says the pipeline should be built **"in MATLAB and
Simulink"** and asks for **"at least two detailed RoadRunner scenes"**. MathWorks
engineers judge this problem statement against their own wording. We do not skip it.

**We do not have to block on it either.** Licence routes, in order:
1. `hackathon@mathworks.com` — MathWorks has partnered with SIH since 2019 and
   provides complimentary software, training and mentoring to participants. The
   SIH-2024 winning team obtained their licence exactly this way.
2. MATLAB **30-day trial** with all toolboxes — covers the whole competition window.
3. **MATLAB Online** + individual Student licence — trivial cost against a ₹1L prize.

Because the Python engine is the algorithm proving ground and the scenario spec is
shared, the MATLAB layer is a *port*, not a rewrite — and the demo never depends on
a licence arriving on time.

> Strategic note: the MATLAB requirement scares teams away from MathWorks problem
> statements. Fewer competitors, and a sponsor that actively mentors and recruits.
> The barrier is the moat.

---

## 8. Non-negotiables

1. **Never plan against ground truth.** Always against fused tracks.
2. **Every number in the report comes from a committed script.** No hand-typed metrics.
3. **Determinism.** Every run is seeded and reproducible from the scenario YAML.
4. **The safety supervisor is monotone.** It may only slow down or widen clearance.
5. **No scenario is hand-tuned to pass.** Parameters are global, or per-class — never per-scenario.
