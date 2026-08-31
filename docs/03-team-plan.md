# Team Plan — 6 people, 2 builders + 4 owners

There are no "fillers" on a winning SIH team. A MathWorks judge scores the
**scenarios, the metrics, the report and the video** as heavily as the algorithm —
and every one of those is owned below by a non-coder. Roughly 55% of the marked
deliverable does not require writing planning code.

## Roles

| Who | Role | Owns | Needs to code? |
|---|---|---|---|
| **B1** | Planning lead | `planning/`, `safety/`, MPC, Hybrid A*, Frenet lattice | Yes |
| **B2** | Perception + platform lead | `perception/`, `prediction/`, Mission Control web UI, MATLAB port | Yes |
| **O1** | **Licence & MathWorks liaison** → then **RoadRunner scene author** | Getting the team licensed; then building the 2 required RoadRunner scenes | No |
| **O2** | **Scenario director** | The 5 scenario specs — geometry, agent scripts, realism review; `scenarios/*.yaml` | YAML only |
| **O3** | **Data & evidence lead** | IDD dataset handling, class taxonomy validation, running the Monte-Carlo campaign, producing every table and plot | Run scripts only |
| **O4** | **Narrative lead** | Technical report, 6-slide deck, demo video, the 3-minute pitch | No |

## Day-1 blocking task — O1, start now

Email **hackathon@mathworks.com** *today*. Template:

> Subject: SIH 2026 — licence request for team working on SIH26037
>
> Hello,
>
> We are a 6-member team from <college> working on MathWorks problem statement
> **SIH26037 — Adaptive Path Planning and Collision Avoidance for Autonomous
> Vehicles on Unstructured Indian Roads** for Smart India Hackathon 2026.
>
> Our institution does not hold a MATLAB campus-wide licence. The expected
> solution requires MATLAB, Simulink and RoadRunner. Could you advise on
> complimentary licence access for SIH participants? We specifically need:
> MATLAB, Simulink, Stateflow, Automated Driving Toolbox, Navigation Toolbox,
> Deep Learning Toolbox, and RoadRunner.
>
> Team lead: <name>, <email>, <phone>. Happy to provide college details.

In parallel, same day, start a **30-day trial** at mathworks.com/campaigns/products/trial
so the team is unblocked immediately regardless of the reply. The trial window
comfortably covers both the 20 Sept idea submission and the grand finale build.

## Parallelisation — nobody waits on anybody

```
B1 ──► planning core ─────────────────────────────► ablations
B2 ──► perception ──► Mission Control ────────────► MATLAB port
O1 ──► licences ────► RoadRunner scene 1 & 2 ─────► scene export
O2 ──► scenario specs (unblocks B1 + O1 + O3) ────► stress variants
O3 ──► IDD taxonomy ──► campaign runs ────────────► every table/plot
O4 ──► report skeleton ──► deck ──────────────────► video + pitch
```

O2's scenario specs are on the critical path for three other people — that work
happens **first**, in YAML, and needs no engine to exist.

## What each owner actually hands in

- **O1** — 2 RoadRunner scenes (village road, unsignalised urban intersection),
  exported and committed, plus a licence-status note in the report.
- **O2** — 5 committed scenario YAMLs matching the PS wording exactly, each with a
  one-page rationale citing a real Indian road situation.
- **O3** — `artifacts/campaign/` : the results table, latency histograms, the
  ablation study, and the A/B against the lane-following baseline.
- **O4** — `docs/report.pdf`, a 6-slide deck, and a ≤3 min demo video.

## The pitch O4 must be able to deliver in 3 minutes

1. **Hook** — "Indian roads don't have lanes. They have negotiation." (10 s)
2. **Gap** — every production stack plans in a lane frame; show the baseline planner
   failing on our market scenario. (30 s)
3. **Idea** — corridor + risk field, not lane + occupancy grid. (40 s)
4. **Live demo** — Mission Control, drag the chaos slider to 1.0, cow walks out,
   wrong-way rider appears, vehicle handles both. (60 s)
5. **Evidence** — the Monte-Carlo table and the ablation. "The risk field bought us
   X% collision reduction; here's the p95 replan latency." (30 s)
6. **Rubric close** — MATLAB/Simulink model, 2 RoadRunner scenes, all five required
   scenarios. (10 s)
