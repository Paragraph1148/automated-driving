# The MATLAB / Simulink / RoadRunner deliverable (B5)

## What we actually have

| Route | Status | Covers |
|---|---|---|
| MATLAB Online **Basic** | **held** — free, indefinite, 20 h/month | MATLAB, Simulink, and 9 commonly used toolboxes |
| MathWorks SIH complimentary licence | requested (O1) | everything, ideally including RoadRunner |
| 30-day full trial | **held in reserve** | everything except RoadRunner |
| Campus-wide licence | none | — |

## The constraint that drives the design

MATLAB Online Basic does **not** include Automated Driving Toolbox, Navigation
Toolbox, Stateflow or RoadRunner, and RoadRunner is a desktop application that
cannot run in MATLAB Online at all. Twenty hours a month is also very little
time to *develop* in, though it is plenty to *demonstrate* in.

So the MATLAB layer is written in two tiers:

**Tier 1 — base MATLAB + Simulink only.** The whole pipeline: corridor, virtual
reference path, Frenet lattice, risk field, behaviour logic, bicycle model. No
toolbox calls at all. Consequence: it runs today, inside the free 20 hours, on
any team member's account. Where a toolbox function would normally be used we
implement the maths ourselves — which is more work but is also *more*
defensible in front of a MathWorks judge, not less: we can show the derivation.

**Tier 2 — toolbox-accelerated, activates when a fuller licence lands.** The
same interfaces, backed by `referencePathFrenet`, `trajectoryGeneratorFrenet`
and `dynamicCapsuleList` (Navigation Toolbox), sensor and scenario blocks from
Automated Driving Toolbox, and the behaviour FSM re-expressed as a genuine
Stateflow chart. Selected at load time by a capability check, so a machine
without the toolboxes silently runs Tier 1.

This is the single most important scheduling decision in the project: **the
MATLAB deliverable is never blocked on a licence arriving.** Tier 1 satisfies
the PS wording ("a working simulation pipeline ... in MATLAB and Simulink")
on its own.

## Do not burn the trial

The 30-day trial started on 1 September expires around 1 October. That covers
the 20 September idea submission but **not** the grand finale build. Keep it
unstarted until either (a) the MathWorks SIH licence is refused, or (b) the
finale date is known. O1 owns this decision.

## Simulink models are generated, not hand-drawn

`.slx` files are binary and cannot be reviewed in a diff, which makes them
unmergeable for a six-person team and unverifiable for a judge. Instead
`matlab/build_model.m` constructs the model programmatically with `add_block`
and `add_line`. The model is therefore reproducible from committed text,
diffable, and regenerable after any change to the algorithm.

## RoadRunner fallback

If RoadRunner access does not arrive, the two required scenes are authored in
**Driving Scenario Designer** (included with Automated Driving Toolbox) and the
report states plainly what was used and why. That is a partial loss against the
PS's "at least two detailed RoadRunner scenes" clause and we should not pretend
otherwise — which is exactly why O1's licence request is a day-one task rather
than a week-three one.
