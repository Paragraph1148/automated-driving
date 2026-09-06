# Mission Control — remaining work

Four regressions Rishabh found in the React rebuild are now fixed; what is
left is listed at the bottom.

## Fixed

**Dragging.** The rebuild sent only `drag`. The server wants
`grab` → `drag` → `drop`: `session.grab(id, x, y)` is what marks a body held,
and `drag` on an unheld body does nothing. The whole pointer model is now
ported from the pre-rewrite viewer rather than reinvented — one Map keyed by
pointerId, because a touch screen sends several at once and a mouse never
does. That brought back with it: pinch to zoom and pan, drag on empty road to
pan, tap-slop so a shaky tap still counts as a tap, and long-press-to-remove,
which is the only way to delete a road user on a phone since no phone has a
shift key.

**Zoom buttons.** Restored, with the anchored zoom the old viewer had: zoom
about the cursor or the pinch midpoint rather than the viewport centre, so
whatever you were looking at stays put. Plus a reset, and a chip that appears
once the view is no longer the one the page chose — without it someone who
zooms in and loses the vehicle has no way of knowing why the screen is empty.

**Last-impact readout.** `payload["events"]` was arriving over the socket the
whole time with nothing rendering it. Back as a chip: kind, detail and
timestamp of the most recent contact or off-road excursion.

**Mobile layout.** Rebuilt against the old design rather than patched. The
viewport takes the top 46svh and the rail below shows one group at a time —
Status, Drop, Layers — with a tab row as a real grid row rather than a fixed
overlay, so nothing has to reserve space for it. 44px zoom buttons, 48px tabs.

## Still not ported

- The guide overlay / first-run tour. Worth having: shown the demo cold, a
  viewer reported back "it's one scene with pre-set traffic", having found
  none of the interaction. That tour was the fix.
- The "Road blocked" banner. `debug.blocked_at` is already drawn as a ring on
  the canvas, but the banner that names it and asks the viewer to move the
  obstruction is gone. It is also the most convincing proof the world is live
  — a recording cannot ask you for help.
- Replay-mode scrubbing. `sarathi replay` shares this template and injects
  frames into `__RUN_DATA__`; that path is untested since the rewrite and
  should be verified before shipping.
- Viewer count in the status pill ("live · 3 watching").
