# Demo fix plan

Everything here started from one report: shown the live demo cold, a viewer
came away with "it's just one scene and pre-determined traffic". They had not
found the ten scenarios, could not tell the world was being computed rather
than replayed, and did not know anything on the road could be dragged, dropped
or removed. Three specific things also looked wrong on the screen.

Ordering rule: fix what looks broken before inviting anyone to look at it. A
guided tour that points at crabbing wrong-way riders and sprinting cattle
advertises the bugs.

Each claim below is a measurement, taken before the change and after it, so a
regression shows up as a number rather than as an argument.

---

## P0 — Simulation correctness  ✅ done

| | before | after |
|---|---|---|
| wrong-way rider, heading error vs its own direction of travel | 67° mean, pinned at the heading cone 90–96% of ticks | 9° mean, never pinned |
| oncoming vehicle, same measure | 36° mean, pinned 38% of ticks | 3° mean, never pinned |
| oncoming vehicle, lateral offset | −2.17 m (right side, for the wrong reason) | −1.06 m, deliberately, vs +0.97 m for with-flow traffic |
| cattle, fraction of ticks stationary | 1–5% against a 45% stop bias | 63–89% |
| cattle, fraction of ticks sitting | none — no such behaviour | 43–61% |
| cattle, top speed | 2.56 m/s (= the `v_desired × 1.6` ceiling) | 1.24 m/s |

1. **Mirrored-frame steer sign.** `WrongWayPolicy` and `OncomingPolicy`
   presented the world mirrored and then negated the steer. A lateral
   acceleration comes back in the `+d` direction of the frame it was computed
   in, and for an agent facing backwards along the reference, mirrored `+d`
   *is* its own left — the two mirrorings already cancel. The negation inverted
   the lateral loop, so the rider steered away from its target, saturated,
   spun, and was clamped by `HEADING_CONE`.
2. **Opposing traffic's own half of the road.** `_mirror_context` mirrored
   `d_nominal` onto itself, and `d_nominal` is the *ego's* preferred offset —
   mirrored, it names the ego's half. Bug 1 was cancelling this, so fixing bug 1
   alone would have driven every oncoming vehicle into the ego's lane. Added
   `Corridor.opposing_offset()`; the context now carries both halves and the
   mirror swaps them.
3. **Cattle in the wrong frame.** `CattlePolicy` drove its speed error off
   `ctx.s_dot`, the component along the *road*, while `step_holonomic`
   integrates `state.speed`, the component along the animal's own body. A cow
   standing across the carriageway reads `s_dot ≈ 0` whatever it is really
   doing, so "graze" was read as "you are already stopped" and commanded full
   acceleration every tick. Rewritten in the body frame, with the wander as a
   random walk on heading and a real `sit` mode. Lying down is cattle-only — a
   stray dog on the same policy still never settles.
4. **Regression tests** — `tests/test_road_user_behaviour.py`, on a small
   isolated scene rather than a scenario file, because the cost of a tick is
   the traffic and a full scenario would add a minute per assertion. The animal
   statistics pool across three seeds; one cow's mode sequence is legitimately
   noisy enough that a per-cow threshold would be flaky rather than strict.

## P1 — Make the page an actual mobile page  ✅ done

| | before | after |
|---|---|---|
| `document.compatMode` | `BackCompat` (quirks mode) | `CSS1Compat` |
| `window.innerWidth` on a 390 px phone | **980** | 390 |
| canvas size on that phone | 643 × 1967 | 390 × 354 |
| desktop stage at 1440 px | 1103 × 747 | 1103 × 747 (unchanged) |

5. **No `<!doctype html>` and no `<meta name="viewport">`.** The phone laid the
   page out at a 980 px virtual viewport and scaled it down, so the
   `max-width:900px` breakpoint already in the stylesheet could never fire.
6. **Canvas scale from one axis.** `resize()` derived the scale from the width
   alone, asking a phone to hold 95 m of road across 390 px and drawing a 4 m
   carriageway 17 px tall. Now fits both axes, and shows less road ahead on a
   narrow screen rather than shrinking everything on it.
7. Phone breakpoint for type, spacing and 44 px touch targets.
8. A settled animal is drawn as a rounded body with no heading tick, and
   carries a `rest` flag in the live and recorded frame — otherwise teaching
   cattle to lie down changes nothing anyone can see.

## P3 — Discoverability  ✅ done

9. **Six-card first-run tour**, each step spotlighting a real element: it is
   live and `Replan` is this frame's own thinking time → ten roads, here → drop
   a road user, the planner has no foreknowledge of it → drag anything,
   including the car → retune the thresholds mid-drive → switch the
   architecture off and watch it fail. Shown once, remembered, re-openable from
   **Guide**. The shared replay artifact gets its own two cards, because that
   page has no palette and no drag.
10. **The scenario picker** is labelled and carries a count, and takes a full
    row of its own on a phone.
11. **A pulsing live dot**, because "live" as a grey 12 px word is the one claim
    on the page nobody takes at face value.

## P2 — Mobile interaction  ⬜ not started

12. Remove is shift-click, which does not exist on touch — needs an eraser mode
    or a long-press.
13. No zoom or pan on any device — pinch, wheel, and buttons.
14. Phone layout: give the canvas the screen and move the controls into a
    bottom sheet with Drop / Tune / Layers tabs.

---

## P0.5 — The ego standing still  ⚠️ one bug fixed, root cause found, not solved

The vehicle is stopped for 23-79% of a run and never reaches its goal on a
~200 m road. This predates all the work above.

### What was fixed

The RSS **standoff** rule clamped the speed cap to a hard zero whenever the ego
came within 2 m of a slow leader in its own track. A kinematic bicycle yaws at
`v·tan(δ)/L`, so at zero speed the steering has no authority at all: a vehicle
forbidden to move can never turn out from behind the thing it is stopped
behind. It waits, and a parked car, a barricade or a cow that has sat down
never moves. The rule produced precisely the wedged vehicle it was written to
prevent.

The cap now falls to a crawl when a way past exists and still to a dead stop
when one does not. The RSS term is unchanged and still reaches zero on its own
below ~0.45 m, so the crawl can only govern *inside* the standoff band, never at
contact range. `tests/test_safety_standoff.py` pins both halves; the first test
fails on the previous code. Zero speed caps per 1200 ticks on
`village_road_unmarked`: **198 → 102** at chaos 0, **300 → 180** at chaos 0.35.

### What it did not fix

Over 60 benchmark runs each side:

| | before | after |
|---|---|---|
| mean progress | 30.8% | 31.4% |
| mean speed | 0.90 m/s | 0.95 m/s |
| collision-free | 45/60 | 43/60 |

A two-run difference in collisions, well inside one standard error at this
sample size, and in the worse direction. **The safety layer was not what was
holding the vehicle.** During a 30.4 s standstill it permits 0.98 m/s on
average and binds at zero only 20% of the time.

### The actual root cause

Instrumented across 636 stopped ticks on `village_road_unmarked` at chaos 0.35:

| stage | what it asks for | verdict |
|---|---|---|
| behaviour layer | **4.62 m/s** | wants to go |
| lattice | ~19 feasible candidates, infeasible in **0%** of ticks | **selects one whose own speed profile is 0.045 m/s** |
| safety supervisor | permits **0.98 m/s** | not binding 80% of the time |

Nothing forbids the vehicle from moving. **It chooses a stop-in-place
trajectory** out of nineteen feasible ones. The trajectory cost is

```
w_risk(12.0)·risk + w_speed(2.4)·speed_error + jerk + offset terms
```

so risk outweighs progress **5:1**. In dense traffic every trajectory that
moves carries risk and standing still carries almost none, so standing still
wins. Both weights are live sliders in the Thresholds panel
(`plan.w_risk`, `plan.w_speed`).

The clinching evidence that this is the wrong trade here: **in all 32
collisions across both benchmark arms the ego was stationary at impact.** Not
one was caused by the ego moving into something. It is struck while standing
still — so on these roads, stopping is not the safe option, and the cost
function is optimising for the wrong thing.

A weight sweep (3 scenarios × 2 seeds each) points the same way:

| `w_risk` | `w_speed` | progress | mean speed | collisions |
|---|---|---|---|---|
| 12.0 | 2.4 *(default)* | 23.9% | 0.93 m/s | 3/6 |
| 8.0 | 4.0 | 24.5% | 0.97 m/s | 3/6 |
| 6.0 | 6.0 | 24.4% | 0.64 m/s | 2/6 |
| 4.0 | 8.0 | **30.3%** | 0.75 m/s | **1/6** |

Biasing toward progress improved progress *and* reduced collisions. The sample
is small and the collision counts are not significant on their own, but the
direction matches the stationary-impact finding exactly.

**Still open.** No configuration completes a scenario (0/6 everywhere), so the
weights are not the whole story. Rebalancing them is a safety-policy decision
rather than a bug fix, which is why it has not been made here.

F1 and F2 below are the two halves of the answer to a vehicle that cannot get
past something: it tries once by itself, and then it says so.

---

## F1 — Ask the viewer for help when the road is walled off  ✅ done

`SafetySupervisor.road_blocked()` answers, explicitly, "is there no clear way
across the carriageway ahead?" — sampled across the corridor, over a longer
horizon than the crawl gate, and only once the vehicle has come to rest so that
slowing for a leader is never reported as being walled in. When it holds for
1.5 s the viewer gets a banner naming the road user in the way and the gesture
that clears it, plus a pulsing ring on that road user.

It is reported, never used for control, and a test pins that the verdict is
identical whether or not it is called.

Two things worth knowing:

* It could not be read off the `boxed-in` branch of the standoff rule. On a
  walled test road the binding rule at the moment the vehicle gave up was
  `leader:parked_vehicle`, so a hint hung off `boxed-in` never appeared at all.
* It returns the **track**, not a track id. Track ids belong to the tracker's
  own numbering, and the viewer holds simulator agents — matching one against
  the other put the marker on an unrelated vehicle 40 m up the road and named
  the wrong class in the banner.

The report is built from what the vehicle *perceives*. A rank of vehicles
abreast at one station occludes itself and the tracker only sees the outermost,
so such a wall reads as passable. That is correct behaviour, and the test uses
a staggered wall for the same reason.

## F2 — Reverse out of a dead end  ✅ built, off by default

A kinematic bicycle yaws at `v·tan(δ)/L`, so a stopped vehicle has **no**
lateral authority at all — it cannot turn on the spot to face a gap. Walled in,
backing up and re-approaching is the only way it can change the angle it
presents. It works: on a walled test road the vehicle shunts once, backing from
s=65.6 to 60.8 and swinging its nose from d=+1.53 to +0.19.

Guards, each of which is a bug that was measured first:

| guard | why |
|---|---|
| one shunt per blockage | without it, four shunts in fifty seconds and no progress — it walks backwards down the road |
| refuse if anything behind is closing | distance is not room: a car 10 m back at 8 m/s leaves 5.8 m of measured gap and covers it in under a second |
| refuse above 12 agents/100 m | backing into a market is not a manoeuvre; the space behind refills with filtering two-wheelers |
| `ControlCommand.reverse` | see below |

**The `v_min` bug.** Reverse was first unlocked by lowering `v_min` in the
integrator. That turns *every hard brake* into a reverse the moment the vehicle
reaches a standstill — it rolls backwards under a braking command nobody meant
as one. It cost 20 collisions in which the ego was drifting backwards at a few
centimetres per second when struck. Reverse is now asked for by the command, not
permitted by the integrator, and that column reads 0.

**Why it is off by default.** Over 60 benchmark runs:

| | reverse off | reverse on |
|---|---|---|
| collision-free | **43/60** | 37/60 |
| mean progress | 31.4% | 31.0% |
| mean speed | 0.95 m/s | 0.99 m/s |
| ego drifting backwards at impact | 0 | 0 |

A six-run difference is about 1.7 standard errors, in the worse direction. The
manoeuvre is right for the situation it was built for and demonstrably escapes
one, but it is not established as safe-neutral across the campaign — so it is a
switch a person turns on (**Thresholds → Ablations → "Reverse out of dead
ends"**), not a default nobody chose. With it off the campaign is identical to
before the feature existed: 43/60, 31.4%.

---

## Appendix: how the standstill was first spotted

Measured over 60 s on `village_road_unmarked`, before P0.5:

| | before P0 | after P0 |
|---|---|---|
| chaos 0.35 | 0.42 m/s mean, stopped 79% of ticks, 25.4 m covered | 0.55 m/s, 65%, 33.3 m |
| chaos 0.0 | 1.45 m/s mean, stopped 23% of ticks, 86.7 m covered | 1.29 m/s, 42%, 77.3 m |

It never reaches the goal in 120 s on a ~200 m road. The P0 work moved this a
little in both directions and is not the cause — at chaos 0 the cattle now
genuinely stand *in* the carriageway instead of drifting out of it, which
legitimately blocks more.

Instrumenting the stopped ticks at chaos 0.0 points at the safety layer, not
at the planner failing to find a path:

* the behaviour layer is asking for **6.64 m/s** on average, and asks for zero
  in only 3% of stopped ticks;
* **19 of 55** candidate trajectories are feasible — there is no shortage;
* but the **RSS safety cap is set in 100% of stopped ticks and is exactly zero
  in 39%** of them;
* and `path_clearance` is **negative in 37%** of stopped ticks — the chosen
  trajectory already overlaps something.

That first read as a livelock in the safety layer. It was one - see P0.5 - but
it was not the cause. The full chain is above.
