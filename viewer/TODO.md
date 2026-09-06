# Mission Control — status

**The live demo is the ORIGINAL viewer.** `sarathi/assets/mission_control.html`
has been restored to the pre-rewrite file, so `uv run sarathi serve` behaves
exactly as it always did. Nothing in this directory is installed.

The React rewrite lives on in `viewer/` and can be resumed whenever. Building
it no longer overwrites the live page — `npm run build` only writes `dist/`,
and installing is a separate, deliberate `npm run stage`.

## Why it was pulled

Toggling an ablation froze the page until a manual reload.

Cause, now fixed in source: every `set` command is answered with
`{"tuned": {...}}`, and `reset_tuning` broadcasts `{"values": {...}}`. The
message router listed the messages that were *not* frames — `meta`, `grabbed`
— and treated everything else as one, so both of those were stored as the
current frame. The draw loop then read `f.ego.h` off an object with no `ego`
and threw on every animation frame from that moment on. The canvas froze and
only a reload cleared it.

The fix is to identify a frame **positively**, by the field only a frame has,
which is what the original viewer did (`if (msg.t === undefined) return;`).
Enumerating the exceptions fails open every time the server grows a new reply.

## Before swapping it back in

- Re-test the ablations against a live server — the fix is in source but has
  not been exercised end to end.
- Audit the rest of the message router the same way: anything the server can
  send that is not a frame needs to be inert, not merely unlisted.
- Replay-mode scrubbing and the rate control. `sarathi replay` shares this
  template and injects frames into `__RUN_DATA__`; untested since the rewrite.
- Chaos and outcome in the header.

## Already ported and working (in `viewer/`, not installed)

Full pointer model (grab/drag/drop, pinch, pan, long-press-to-remove, tap
slop), zoom cluster with anchored zoom, the eight-entry drop palette carrying
policy and speed, Min TTC with warn/alert banding, Reset vehicle, the road
blocked banner, the road-user legend, clock, viewer count, last-event chip,
the mobile tab layout, and the seven-card guided tour.
