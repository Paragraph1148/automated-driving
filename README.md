# SARATHI

**Situation-Aware Risk-Adaptive Trajectory & Hazard Intelligence** — adaptive path
planning and collision avoidance for autonomous vehicles on unstructured Indian
roads.

> **SIH 2026 · SIH26037 · MathWorks · Robotics and Drones**

A full closed-loop driving stack — simulated sensors, tracking, multi-modal
prediction, a continuous risk field, a corridor solver, a behaviour machine, a
jerk-minimal trajectory lattice and an RSS safety supervisor — running at 20 Hz
on an ordinary laptop CPU, with a live browser console you can interfere with
while it drives.

---

## Quickstart

You need [uv](https://docs.astral.sh/uv/getting-started/installation/). Nothing
else — not even a Python install; uv fetches the right interpreter itself.

```bash
# install uv once
curl -LsSf https://astral.sh/uv/install.sh | sh          # macOS / Linux
powershell -c "irm https://astral.sh/uv/install.ps1|iex" # Windows

git clone https://github.com/Paragraph1148/automated-driving
cd automated-driving

uv sync                 # creates .venv and installs everything (~30 s, once)
uv run sarathi serve    # starts the live demo
```

Open **<http://localhost:8420>**.

`uv sync` is optional — `uv run` will do it for you on first use — but running it
explicitly makes the one-time setup visible instead of hiding it inside the first
launch.

Requirements: Python 3.11+ (uv provides it), no GPU, no cloud, no MATLAB licence.
The port is configurable: `uv run sarathi serve --port 9000`.

---

## The live demo

| Do this | And this happens |
|---|---|
| **Click** empty road | drops the selected road user there, live |
| **Drag** any vehicle | moves it while the world keeps running |
| **Shift-click** a road user | removes it |
| Scenario dropdown | switches roads without a restart |
| **Thresholds** panel | all 31 tunables, adjustable mid-run |
| **Ablations** | switch the risk field, the prediction or RSS off and watch it degrade |

Nothing is scripted. The planner has no more foreknowledge of a hand-placed cow
than of anything else in the scene, and the code answering the click is the code
in the submission — which is the point. A recorded video invites the question
"how do we know it isn't a replay?"; someone who places the obstacle themselves
has already answered it.

### Putting it on the internet

```bash
./scripts/share.sh
```

Serves the demo and opens a Cloudflare quick tunnel to it, printing an
`https://…trycloudflare.com` URL anyone can open. No account, no card, no
server — and it works only because the page and the telemetry socket share one
port, so a single tunnel carries both and the viewer derives `wss://` from the
page's own origin.

For a link that outlives the terminal there is a Codespaces devcontainer, a
Dockerfile, and a one-command Oracle setup script.
**[docs/05-hosting.md](docs/05-hosting.md)** ranks the hosts by what they ask you
to pay with, has the measurements behind the choice — a world costs most of a
CPU core, a spectator costs 128 KiB/s and no CPU — and explains why a serverless
runtime cannot serve this at all.

---

## Every command

```bash
uv run sarathi list                            # the ten scenarios
uv run sarathi serve                           # live interactive demo
uv run sarathi serve --scenario market_dense_mixed --port 9000
uv run sarathi serve --host 0.0.0.0            # reachable from off the machine
uv run sarathi run village_road_unmarked       # one headless run
uv run sarathi run all --chaos 0.7             # every scenario, harder
uv run sarathi run all --controller baseline   # the lane-following comparison
uv run sarathi run market_dense_mixed --record run.json
uv run sarathi replay run.json -o page.html    # shareable replay page
uv run --extra dev pytest                      # 155 tests
uv run python scripts/hostcheck.py             # can this box hold 20 Hz?
./scripts/share.sh                             # public URL via a tunnel
```

`--chaos 0..1` is a single knob over the whole scene: wrong-way riders, driver
aggression, cattle, barricades and lane-marking visibility all scale with it.

---

## Results

Ten scenarios × twenty seeds × two controllers, on identical seeds and identical
sensor noise — **400 runs**. Everything below comes out of `scripts/benchmark.py`
and is summarised by `scripts/analyse_campaign.py`; nothing here is typed in by
hand. Replan latency is measured in a separate, uncontended run, for the reason
given below.

| | SARATHI | Lane-following baseline |
|---|---|---|
| Runs collision-free | 123 / 200 (62 %) | 140 / 200 (70 %) |
| 95 % CI on that | [55 %, 68 %] | [63 %, 76 %] |
| Mean route progress | **29.3 %** [27.2, 31.3] | 27.1 % [25.4, 28.7] |
| Scenarios where it gets further | **8 of 10** | 2 of 10 |
| Distance covered | 9.4 km | 8.0 km |
| Contacts per 100 km | 820 | 748 |
| …of which we drove into | 714 | 549 |
| Routes completed | 1 / 200 | 0 / 200 |
| Replan latency, median p95 | 24.6 ms | — |

Per scenario, mean route progress and runs that ended without contact:

| Scenario | Ours | Baseline | Clean (ours) | Clean (base) |
|---|---|---|---|---|
| Bus stop overtake | 22.8 % | 21.9 % | 13/20 | 13/20 |
| Cattle crossing | 34.7 % | 35.2 % | 16/20 | 14/20 |
| Construction diversion | 22.2 % | 20.9 % | 12/20 | 16/20 |
| Highway merge | 45.6 % | 33.2 % | 18/20 | 19/20 |
| Dense market | 15.9 % | 13.0 % | 4/20 | 10/20 |
| Narrow bridge, oncoming | 43.6 % | 42.7 % | 20/20 | 20/20 |
| Night, wrong-way rider | 22.5 % | 20.0 % | 12/20 | 12/20 |
| School zone | 30.1 % | 27.5 % | 9/20 | 14/20 |
| Unsignalled junction | 34.4 % | 33.3 % | 14/20 | 9/20 |
| Village road, unmarked | 20.7 % | 22.9 % | 5/20 | 13/20 |

**What that does and does not say.** It gets further than a lane-based planner
without ever reading a lane marking, on 8 of the 10 scenarios. It also has
more contacts — 62 % of runs clean against the baseline's 70 %. **Neither
difference is statistically significant**: Fisher's exact test on the safety
comparison gives *p* = 0.09, and the two progress intervals overlap. On 200 runs
per arm this campaign still cannot separate the two controllers, and any claim
that it can — in either direction — is unsupported.

Where the contacts are is more informative than the total. They concentrate in
the cluttered scenes: the dense market (4/20 clean against the baseline's 10/20),
the unmarked village road (5/20 against 13/20), the school zone (9/20 against
14/20). The pattern is consistent and it is not flattering: our planner pushes
into gaps the baseline refuses to enter, and pays for it. The baseline earns much
of its cleaner record by stopping — it covers 8.0 km to our 9.4 km, and completes
no route at all. Neither controller is close to finishing routes: **1 of 200 for
us, 0 of 200 for it.**

Each contact is recorded with our own speed and the bearing of the other body, so
"we drove into it" and "it drove into us while we were stopped" are counted
separately: 67 of our 77 contacts were ours to avoid, against 44 of the
baseline's 60.

**On latency.** Replan time is the one metric here that is not deterministic —
it is wall-clock, so a busy machine corrupts it. Measured on an **Intel Core
i7-1255U** with two workers on six cores, the median run's p95 is **24.6 ms**,
about half the 50 ms a 20 Hz tick allows, and 28 of 30 runs stay inside that
budget; the dense market (50.8 ms) and the unsignalled junction (59.3 ms) exceed
it. Running the same campaign with more workers than physical cores inflated the
figure **3.1×**, to a median p95 of 76.5 ms. If you re-measure, use
`--workers` well under your core count and pass the result to
`analyse_campaign.py --latency`.

**These numbers are lower than the ones this file used to report** (43.3 % versus
36.2 % progress, on 30 runs). That is not a regression in the planner. The
simulation itself was wrong in ways that made it easier: cattle drifted out of
the carriageway instead of standing in it, and wrong-way riders were steered by a
sign error rather than actually riding against the flow. Fixing those made every
number fall — including the **lane-following baseline's, whose code has not been
touched since `97680a6`**. Identical code, same seeds, 36.2 % → 26.8 %. When a
control you did not modify moves with the treatment, the world moved, not the
planner.

The baseline is a fair comparison, not a straw man: it sees the same sensors, the
same noise and the same seeds, and it mostly fails by stopping rather than by
crashing — which is why the collision counts are close while the progress numbers
are not.

---

## The idea

Every production autonomous-driving stack assumes a **lane**. Indian roads do not
have lanes — they have a *negotiated, continuously deforming free space* shared by
buses, auto-rickshaws, two-wheelers filtering through 60 cm gaps, pushcarts,
pedestrians crossing wherever they like, and cattle.

SARATHI replaces the two primitives that break down there:

| A conventional stack uses | SARATHI uses instead |
|---|---|
| Lane centreline as the planning frame | **Drivable corridor** — a dynamic program over the free space ahead, re-solved every tick |
| Binary occupancy grid | **Risk field** — continuous, class-conditioned, harm-weighted, indexed by time |
| One predicted trajectory per agent | **Multi-modal intent** — cut in, filter, dart, ride the wrong way — with covariance that grows |
| Obstacles are walls | Potholes are **traversable cost**; the verge is drivable |
| Lane-change state machine | **Eight behaviours**, wrong-way evasion included |

Because it never needed lane markings, it degrades gracefully when they are
faded, absent, or simply wrong. There is no branch anywhere in the code for
"markings missing".

## How it works

Eight stages, twenty times a second:

| | Stage | What it does | Where |
|---|---|---|---|
| 1 | **Sense** | Camera, LiDAR and radar with range noise, dropout, class confusion and geometric occlusion. The planner never sees ground truth. | `perception/sensors.py` |
| 2 | **Fuse** | Constant-velocity Kalman tracks; class fused in log-odds so two confident sightings can disagree. | `perception/fusion.py` |
| 3 | **Predict** | A manoeuvre distribution per agent, conditioned on its class, with covariance clamped to what it can physically reach. | `prediction/intent.py` |
| 4 | **Risk field** | Each hypothesis becomes an anisotropic kernel — flat core the size of the body, skirt sized by uncertainty, weight set by how badly hitting it would hurt. | `planning/risk.py` |
| 5 | **Corridor** | A dynamic program over (distance, offset) finds the cheapest ribbon of free space. This is what replaces the lane. | `planning/corridor_path.py` |
| 6 | **Behaviour** | Eight states — cruise, follow, nudge, overtake, yield, creep, wrong-way evade, emergency stop — with dwell times so it cannot chatter. Maps 1:1 onto Stateflow. | `planning/behaviour.py` |
| 7 | **Lattice** | Jerk-minimal quintic/quartic polynomials in the corridor's Frenet frame, sampled inside the reachable set and scored against the risk field in space *and* time. | `planning/lattice.py` |
| 8 | **Assure** | RSS recalibrated for Indian gap acceptance, inverted in closed form to a safe speed, then a control-barrier filter. Monotone: it can only slow the vehicle down. | `safety/rss.py` |

The traffic around the vehicle is its own contribution: a non-lane-based IDM
where interaction strength depends on how much two bodies overlap laterally
rather than on which lane they are in, across twelve road-user classes, with
gap-seeking, wrong-way riders and cattle that do not model the ego at all.

---

## Reproducing every number

```bash
uv run python scripts/benchmark.py --seeds 3        # -> artifacts/benchmark.json
uv run --extra dev python scripts/capture.py \
    --scenario bus_stop_overtake --behaviour OVERTAKE \
    --min-clearance 0.15 -o artifacts/fig-console.png
```

`benchmark.py` runs every scenario under both controllers across seeds and writes
one JSON with a row per run. Both presentation decks in `ppt/` read that file and
refuse to build without it, so a figure on a slide cannot drift away from what the
code actually does.

`capture.py` starts the real server, drives a real browser, and waits until the
telemetry satisfies a condition you give it before it takes the screenshot — so a
figure is a moment that was chosen, not one that happened to be caught. It needs
the dev extra (`--extra dev`) for Playwright.

---

## Layout

```
sarathi/          the stack — runs anywhere, no licence required
  core/          types & Indian road-user taxonomy, geometry, Frenet, kinematics
  world/         drivable corridor, surface defects, scenario loader
  agents/        NLB-IDM + the Indian behaviour zoo
  perception/    sensor models, fusion, tracking
  prediction/    multi-modal intent prediction
  planning/      risk field, corridor path, Frenet lattice, behaviour FSM
  safety/        RSS-India + control-barrier supervisor
  metrics/       run metrics — progress, clearance, jerk, replan latency
  sim/           simulator, recorder, snapshots for the viewer
  assets/        the Mission Control viewer (one HTML file)
  serve.py       live interactive server
scenarios/       *.yaml — one spec per scenario, shipped inside the package
scripts/         benchmark.py (the evidence), capture.py (the figures),
                 hostcheck.py (can this machine hold 20 Hz?),
                 share.sh (a public URL in one command)
tests/           155 tests, including behavioural regressions
docs/            architecture, problem statement, team plan, MATLAB bridge,
                 hosting
deploy/          Dockerfile, compose, Caddyfile, systemd unit, Oracle setup,
                 redeploy (ship a change to a running host),
                 COMMANDS.txt (every command, in the order you need them),
                 caddy/ (one file per site, for sharing a box between projects)
.devcontainer/   GitHub Codespaces, for hosting without a credit card
ppt/             the SIH idea submission deck and the internal briefing
artifacts/       benchmark.json and the figures the decks embed
```

Start with **[docs/00-architecture.md](docs/00-architecture.md)** — it is the plan
of record. **[docs/03-team-plan.md](docs/03-team-plan.md)** has the six-person work
split; **[docs/04-matlab-bridge.md](docs/04-matlab-bridge.md)** covers the licence
situation and the two-tier MATLAB design.

## Status

| Batch | Scope | State |
|---|---|---|
| B0 | Architecture, PS decode, team plan | done |
| B1 | Core sim, corridor, NLB-IDM traffic model | done |
| B2 | Perception, prediction, risk field | done |
| B3 | Hierarchical planner + safety supervisor | done; still more cautious than it needs to be |
| B4 | Mission Control, live interactive demo | done |
| B5 | MATLAB / Simulink / Stateflow / RoadRunner | not started |
| B6 | Monte-Carlo campaign, ablations, report | campaign done; ablations and report next |

Known and open: the vehicle is over-cautious in dense traffic, two scenarios
account for four of the five contacts, prediction priors are hand-built rather
than fitted to data, and the dense-market scene exceeds the 20 Hz replan budget.
Each is measured on every run rather than tuned around.

## Licence

MIT.
