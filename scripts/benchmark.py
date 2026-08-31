"""Run every scenario under both controllers across several seeds.

This is the source of every performance number we quote. It writes a JSON row
per run plus a summary table, so a claim in a slide or in the report can always
be traced back to a command anyone can re-run:

    uv run python scripts/benchmark.py --seeds 5 -o artifacts/benchmark.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import traceback
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from sarathi.metrics.run_metrics import RunMetrics
from sarathi.paths import scenario_dir
from sarathi.planning.baseline import BaselineLaneFollower
from sarathi.planning.sarathi import SarathiController
from sarathi.sim.simulator import Simulator
from sarathi.world.scenario import load_scenario

CONTROLLERS = {"sarathi": SarathiController, "baseline": BaselineLaneFollower}


def one(job):
    """Run a single (scenario, controller, seed). Never raises.

    A campaign that dies on run 25 of 60 tells you nothing about the other 35.
    A failed run is reported as a failed run - it is a result, and a loud one.
    """
    path, controller, seed, chaos = job
    try:
        scenario = load_scenario(Path(path), chaos=chaos, seed=seed)
        sim = Simulator(scenario, CONTROLLERS[controller]())
        row = sim.run(verbose=False).metrics.as_row()
    except Exception as exc:                       # noqa: BLE001 - reported, not hidden
        row = RunMetrics(scenario=Path(path).stem, seed=seed,
                         chaos=chaos or 0.0).as_row()
        row["error"] = f"{type(exc).__name__}: {exc}"
        row["traceback"] = traceback.format_exc()
    row["controller"] = controller
    return row


def outcome(row):
    if row.get("error"):
        return "ERROR"
    if row["collision"]:
        return "crash" if row["impact_speed"] >= 0.5 else "contact"
    if row["left_corridor"]:
        return "off-road"
    if row["completed"]:
        return "complete"
    return "stuck"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--first-seed", type=int, default=1)
    ap.add_argument("--chaos", type=float, default=None)
    ap.add_argument("--scenarios-dir", default=None)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--controllers", default=",".join(CONTROLLERS),
                    help="comma-separated subset to run")
    ap.add_argument("-o", "--out", default="artifacts/benchmark.json")
    args = ap.parse_args()

    paths = sorted(scenario_dir(args.scenarios_dir).glob("*.yaml"))
    seeds = list(range(args.first_seed, args.first_seed + args.seeds))
    wanted = [c.strip() for c in args.controllers.split(",") if c.strip()]
    unknown = [c for c in wanted if c not in CONTROLLERS]
    if unknown:
        raise SystemExit(f"unknown controller(s): {', '.join(unknown)}")
    jobs = [(str(p), c, s, args.chaos)
            for p in paths for c in wanted for s in seeds]

    rows = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for i, row in enumerate(pool.map(one, jobs), 1):
            rows.append(row)
            print(f"[{i:3d}/{len(jobs)}] {row['controller']:<8s} "
                  f"{row['scenario']:<28s} seed={row['seed']:<3d} "
                  f"{outcome(row):<8s} progress={row['goal_progress']*100:5.1f}% "
                  f"p95={row['replan_ms_p95']:5.1f}ms", flush=True)

    summary = {}
    for controller in wanted:
        mine = [r for r in rows if r["controller"] == controller]
        by_scenario = {}
        for name in sorted({r["scenario"] for r in mine}):
            runs = [r for r in mine if r["scenario"] == name]
            by_scenario[name] = {
                "runs": len(runs),
                "collision_free": sum(not r["collision"] for r in runs),
                "mean_progress": statistics.fmean(r["goal_progress"] for r in runs),
                "mean_speed": statistics.fmean(r["mean_speed"] for r in runs),
                "min_clearance": min(r["min_clearance"] for r in runs),
                "jerk_rms": statistics.fmean(r["jerk_rms"] for r in runs),
                "p95_ms": max(r["replan_ms_p95"] for r in runs),
                "outcomes": [outcome(r) for r in runs],
            }
        summary[controller] = {
            "runs": len(mine),
            "errors": sum(bool(r.get("error")) for r in mine),
            "collision_free": sum(not r["collision"] for r in mine),
            "scenarios_always_collision_free": sum(
                v["collision_free"] == v["runs"] for v in by_scenario.values()),
            "scenarios": len(by_scenario),
            "mean_progress": statistics.fmean(r["goal_progress"] for r in mine),
            "mean_jerk_rms": statistics.fmean(r["jerk_rms"] for r in mine),
            "worst_p95_ms": max(r["replan_ms_p95"] for r in mine),
            "worst_max_ms": max(r["replan_ms_max"] for r in mine),
            "by_scenario": by_scenario,
        }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"seeds": seeds, "chaos": args.chaos,
                               "summary": summary, "rows": rows}, indent=1))
    print(f"\nwrote {out}")
    for controller, s in summary.items():
        print(f"{controller:<9s} collision-free {s['collision_free']}/{s['runs']} runs, "
              f"{s['scenarios_always_collision_free']}/{s['scenarios']} scenarios, "
              f"mean progress {s['mean_progress']*100:.1f}%, "
              f"worst p95 {s['worst_p95_ms']:.1f} ms")


if __name__ == "__main__":
    main()
