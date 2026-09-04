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

## Found while verifying, not yet scoped

**The ego barely drives, and this predates all of the above.** Measured over
60 s on `village_road_unmarked`:

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

That reads as a livelock: the vehicle is too close to move, and cannot stop
being too close without moving. Worth a decision before P2, because a demo
whose vehicle stands still undercuts every other fix here.
