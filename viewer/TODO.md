# Mission Control — regressions against the old viewer

Found by Rishabh testing the React rebuild. Not yet fixed; portfolio work
came first. Each of these worked in the pre-rewrite `mission_control.html`,
so they are regressions rather than new features.

## 1. Dragging road users is broken

`Viewport.tsx` hit-tests and sets `dragging.current`, then sends
`{cmd:"drag", id, x, y}` on pointer move. But the protocol in `serve.py`
wants a **`grab` first** — `session.grab(id, x, y)` is what marks the body
held, and the server replies `{grabbed: …}`. Without it `drag` is being
sent for a body the session does not consider held, and `drop` is never
sent on release either. Wire the full grab → drag → drop sequence, and
feed `held` back so the accent ring draws.

## 2. Zoom buttons gone

Not intentional — an oversight. The old viewer had explicit `+` / `−`
buttons; the rebuild only kept `wheel`. That is unusable on a touch device,
which is most of the point of the demo. Restore the buttons (and pinch),
clamped to the existing `ZOOM_MIN` / `ZOOM_MAX`.

## 3. Last-impact readout gone

The old viewer reported the last contact — time, and whether the vehicle
left the carriageway. `payload["events"]` is still arriving over the socket
and `Frame.events` is typed, but nothing renders it. Restore it; it is the
single most useful thing on screen when something goes wrong, and its
absence makes the demo look like nothing ever fails.

## 4. Mobile layout is a regression

The old viewer was usable on a phone; the rebuild is not. `.stage` collapses
to a 1fr/auto grid with the rail taking `38vh`, which leaves the viewport a
letterbox, and the top bar wraps into several rows. Rebuild the small-screen
layout properly against the old one rather than patching this — the old
markup is in git history at `sarathi/assets/mission_control.html` before the
rewrite commit.

## 5. Also not yet ported (known, from the rewrite)

- The guide overlay.
- Replay-mode scrubbing. `sarathi replay` shares this template and injects
  frames into `__RUN_DATA__`; that path is untested since the rewrite and
  should be verified before shipping.
- Pan by dragging empty road.
