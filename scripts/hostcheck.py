"""Can this machine actually run the live demo at 20 Hz?

The answer is not obvious on a free-tier VM. The stack has 50 ms per tick and
spends 30-40 ms of it on a fast x86 core; an Ampere A1 core — what Oracle's
always-free tier gives you — is slower per thread, so the same work can land the
wrong side of the budget and the demo runs in visible slow motion. That is a bad
thing to discover in front of a judge.

Run this on the host *before* demo day:

    uv run python scripts/hostcheck.py
    uv run python scripts/hostcheck.py --scenario market_dense_mixed

It reports the step cost against the tick budget and, because the simulation is
Python-level work that the GIL pins to one thread, how many concurrent
*processes* the machine has cores for. It does not tell you how many people can
watch: one process broadcasts to all of them, so viewers cost bandwidth
(~125 KiB/s each), not CPU. See docs/05-hosting.md.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import time

from sarathi.serve import LiveSession

#: Frames go out on every other tick, so this is the real per-viewer stream.
FRAME_HZ = 10.0


def measure(scenario: str, ticks: int, warmup: int) -> dict:
    session = LiveSession(scenario, seed=1)
    for _ in range(warmup):
        session.step()

    steps, frames, payload = [], [], 0
    for i in range(ticks):
        t0 = time.perf_counter()
        session.step()
        steps.append((time.perf_counter() - t0) * 1e3)
        if i % 2 == 0:
            t0 = time.perf_counter()
            payload = len(json.dumps(session.frame()))
            frames.append((time.perf_counter() - t0) * 1e3)

    steps.sort()
    budget_ms = session.sim.dt * 1e3
    per_tick = statistics.median(steps) + statistics.median(frames) / 2
    return {"scenario": scenario, "budget_ms": budget_ms,
            "step_median_ms": statistics.median(steps),
            "step_p95_ms": steps[int(0.95 * len(steps)) - 1],
            "frame_median_ms": statistics.median(frames),
            "per_tick_ms": per_tick,
            "realtime_factor": min(1.0, budget_ms / per_tick),
            "payload_kib": payload / 1024,
            "stream_kib_s": payload * FRAME_HZ / 1024}


def demo_already_running(port: int = 8420) -> bool:
    """Is something already serving on the demo's port?

    It almost certainly is, on a deployed host: the service runs with
    --keep-warm, so it holds the core continuously. Timing against that measures
    two processes sharing one core and reports roughly half the truth, which
    reads as a machine that cannot hold real time when it can.
    """
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz",
                                    timeout=1.0) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", action="append", default=None,
                    help="scenario to time (repeatable); default is the demo "
                         "default and the heaviest scene")
    ap.add_argument("--ticks", type=int, default=200)
    ap.add_argument("--warmup", type=int, default=20)
    args = ap.parse_args()

    scenarios = args.scenario or ["village_road_unmarked", "market_dense_mixed"]
    cores = os.cpu_count() or 1
    print(f"{platform.machine()}  {cores} core(s)  {platform.python_version()}\n")

    if demo_already_running():
        print("WARNING: a demo is already serving on :8420, and on one core it is")
        print("         competing with this measurement for it. Every number below")
        print("         will be roughly twice what the machine can really do.")
        print("         Stop it first:  sudo systemctl stop sarathi\n")

    results = [measure(s, args.ticks, args.warmup) for s in scenarios]
    print(f"{'scenario':<26} {'step':>8} {'p95':>8} {'frame':>7} "
          f"{'budget':>8} {'speed':>7}")
    for r in results:
        print(f"{r['scenario']:<26} {r['step_median_ms']:7.1f}ms "
              f"{r['step_p95_ms']:6.1f}ms {r['frame_median_ms']:5.1f}ms "
              f"{r['budget_ms']:6.0f}ms {r['realtime_factor']:6.2f}x")

    # Rank by how much of the budget a tick eats, not by the clamped speed:
    # everything that fits reports 1.00x, so the clamped figure ties.
    worst = max(results, key=lambda r: r["per_tick_ms"] / r["budget_ms"])
    print()
    if worst["realtime_factor"] >= 0.95:
        print(f"OK — holds real time on every scene tested "
              f"(worst: {worst['scenario']} at {worst['realtime_factor']:.2f}x).")
    else:
        print(f"SLOW — {worst['scenario']} runs at "
              f"{worst['realtime_factor']:.2f}x real time on this machine.")
        print("       The demo still works; the world just moves that much")
        print("       slower than the clock. Serve a lighter default scenario,")
        print("       or host on something with a faster single core.")

    print(f"\nOne process per concurrent *world*: {max(1, cores - 1)} here, "
          f"leaving a core for the proxy.")
    print(f"Viewers are free in CPU and cost {worst['stream_kib_s']:.0f} KiB/s "
          f"each ({worst['stream_kib_s'] * 3600 / 1e6:.1f} GB per viewer-hour).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
