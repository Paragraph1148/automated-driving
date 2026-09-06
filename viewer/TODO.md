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

**The drop palette.** I had invented a list instead of reading the old one.
The real palette is eight entries carrying `[cls, label, policy, speed]`, and
the policy is the half that matters: "Wrong-way" and "Rash driver" are not
classes, they are a two-wheeler and a car given a different policy. My `place`
command sent neither policy nor speed, so those two were impossible to create
and the palette collapsed into duplicates of the plain classes.

**Min TTC, and severity on the tiles.** The telemetry block was missing Min TTC
entirely, and the warn/alert banding on TTC (<4s, <2s) and path clearance
(<0.7m, <0.3m) that makes the number that matters findable at a glance.

**Reset vehicle.** `cmd: restart_ego` had no control at all.

**Road-user legend, clock, viewer count, and the "Road blocked" banner** — the
last of which is the most convincing proof the world is live, since a
recording cannot ask you for help.

**The guided tour.** Seven cards, shown once on a first visit, remembered in
localStorage and re-openable from the Guide button in the header. One change
from the original: it used to drop any step whose target was hidden, which on
a phone meant the palette and ablations steps silently vanished — the viewer
who most needs the tour got the least of it. This version opens the tab or the
sheet the step points at first.

## Still not ported

- Replay-mode scrubbing and the rate control. `sarathi replay` shares this
  template and injects frames into `__RUN_DATA__`; that path is untested
  since the rewrite and should be verified before shipping.
- The scenario/chaos/outcome trio in the header — scenario is a select now,
  but chaos and outcome are not shown.
