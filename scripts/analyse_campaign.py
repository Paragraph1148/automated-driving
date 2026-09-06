"""Turn a benchmark JSON into claims the data can actually support.

A campaign summary that reports "25/30 collision-free" invites two mistakes at
once. It invites *us* to quote the flattering half, and it invites a reader to
conclude the difference from a baseline is real when 30 runs cannot resolve it.
Both were live in this repo's README.

So this reports, for every headline number, the interval around it - and for the
safety comparison, whether the difference survives a test at all. It also
reports collisions per kilometre travelled, because a per-run rate silently
punishes the planner that covers more ground: a controller that stops at the
first obstacle is collision-free by not going anywhere.

    uv run python scripts/analyse_campaign.py artifacts/benchmark-v2.json
    uv run python scripts/analyse_campaign.py artifacts/benchmark-v2.json --readme
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path

from scipy.stats import fisher_exact

#: One tick of a 20 Hz control loop, milliseconds. Replanning has to finish
#: inside this or the loop is not running at the rate it claims.
TICK_BUDGET_MS = 50.0
#: A contact the ego was not moving for, arriving from behind, is a different
#: event from one it drove into: something hit *it*. Counted separately rather
#: than excluded - it is still a failure, just not the same failure.
STRUCK_SPEED = 0.5
STRUCK_BEARING = 90.0


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Binomial CI that stays inside [0,1] and behaves at the extremes."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def mean_ci(xs: list[float], z: float = 1.96) -> tuple[float, float, float]:
    if not xs:
        return (0.0, 0.0, 0.0)
    m = statistics.fmean(xs)
    if len(xs) < 2:
        return (m, m, m)
    se = statistics.stdev(xs) / math.sqrt(len(xs))
    return (m, m - z * se, m + z * se)


def classify(row: dict) -> str:
    if row.get("error"):
        return "error"
    if not row["collision"]:
        return "clean"
    if (row.get("ego_speed_at_impact", 0.0) < STRUCK_SPEED
            and abs(row.get("impact_bearing_deg", 0.0)) > STRUCK_BEARING):
        return "struck-while-stopped"
    return "drove-into"


def arm(rows: list[dict]) -> dict:
    n = len(rows)
    clean = sum(1 for r in rows if not r["collision"] and not r.get("error"))
    kinds = [classify(r) for r in rows]
    km = sum(r.get("distance", 0.0) for r in rows) / 1000.0
    crashes = sum(1 for k in kinds if k in ("drove-into", "struck-while-stopped"))
    at_fault = sum(1 for k in kinds if k == "drove-into")
    prog = [r["goal_progress"] for r in rows if not r.get("error")]
    m, lo, hi = mean_ci(prog)
    return {
        "runs": n, "clean": clean, "clean_ci": wilson(clean, n),
        "km": km, "crashes": crashes, "at_fault": at_fault,
        "struck": sum(1 for k in kinds if k == "struck-while-stopped"),
        "per_100km": (crashes / km * 100.0) if km else float("nan"),
        "at_fault_per_100km": (at_fault / km * 100.0) if km else float("nan"),
        "progress": m, "progress_lo": lo, "progress_hi": hi,
        "mean_speed": statistics.fmean(
            [r["mean_speed"] for r in rows if not r.get("error")] or [0.0]),
        "p95": max([r["replan_ms_p95"] for r in rows] or [0.0]),
        "p95_median": statistics.median(
            [r["replan_ms_p95"] for r in rows] or [0.0]),
        "in_budget": sum(1 for r in rows
                         if r["replan_ms_p95"] <= TICK_BUDGET_MS),
        "completed": sum(1 for r in rows if r.get("completed")),
        "errors": sum(1 for r in rows if r.get("error")),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--readme", action="store_true",
                    help="emit a markdown block ready to paste into README.md")
    ap.add_argument("--latency", metavar="JSON",
                    help="take the latency row from this campaign instead. Replan "
                         "time is wall-clock, so it is the one metric a crowded "
                         "machine corrupts: running more workers than cores "
                         "inflated it 3.1x here. Measure it in its own run with "
                         "workers well under core count, and pass that file.")
    args = ap.parse_args()

    data = json.loads(Path(args.path).read_text())
    rows = data["rows"]
    lat_rows = rows
    if args.latency:
        lat_rows = json.loads(Path(args.latency).read_text())["rows"]
    names = sorted({r["controller"] for r in rows})
    arms = {c: arm([r for r in rows if r["controller"] == c]) for c in names}
    lat = {c: arm([r for r in lat_rows if r["controller"] == c])
           for c in sorted({r["controller"] for r in lat_rows})}

    print(f"campaign: {Path(args.path).name}   "
          f"{len(rows)} runs   seeds {len(data.get('seeds', []))}   "
          f"chaos {data.get('chaos') if data.get('chaos') is not None else 'per-scenario'}")
    print()
    for c, a in arms.items():
        lo, hi = a["clean_ci"]
        print(f"  {c}")
        print(f"    collision-free      {a['clean']}/{a['runs']} = "
              f"{a['clean']/a['runs']:.1%}   95% CI [{lo:.1%}, {hi:.1%}]")
        print(f"    of the failures     {a['at_fault']} drove into something, "
              f"{a['struck']} struck while stopped")
        print(f"    distance covered    {a['km']:.1f} km")
        print(f"    contacts /100 km    {a['per_100km']:.1f}  "
              f"(at fault only: {a['at_fault_per_100km']:.1f})")
        print(f"    route progress      {a['progress']:.1%}   95% CI "
              f"[{a['progress_lo']:.1%}, {a['progress_hi']:.1%}]")
        print(f"    mean speed          {a['mean_speed']:.2f} m/s")
        print(f"    reached the goal    {a['completed']}/{a['runs']}")
        src = lat.get(c, a)
        print(f"    replan p95          {src['p95_median']:.1f} ms median, "
              f"{src['p95']:.1f} ms worst"
              + ("" if args.latency else "   <- wall-clock; see --latency"))
        print(f"    inside 20 Hz budget {src['in_budget']}/{src['runs']} runs "
              f"have p95 <= {TICK_BUDGET_MS:.0f} ms")
        if a["errors"]:
            print(f"    ERRORS              {a['errors']}")
        print()

    if len(names) == 2:
        a, b = arms[names[0]], arms[names[1]]
        table = [[a["clean"], a["runs"] - a["clean"]],
                 [b["clean"], b["runs"] - b["clean"]]]
        _, p = fisher_exact(table)
        verdict = ("no significant difference" if p >= 0.05
                   else f"significant at p<0.05 in favour of "
                        f"{names[0] if a['clean']/a['runs'] > b['clean']/b['runs'] else names[1]}")
        print(f"  safety, {names[0]} vs {names[1]}:  Fisher exact p = {p:.3f}"
              f"  -> {verdict}")
        print(f"  progress CIs {'overlap' if a['progress_lo'] < b['progress_hi'] and b['progress_lo'] < a['progress_hi'] else 'do not overlap'}")
        print()

    if args.readme:
        print("\n" + "=" * 72 + "\nPASTE BELOW INTO README.md\n" + "=" * 72 + "\n")
        s, base = arms.get("sarathi"), arms.get("baseline")
        if not (s and base):
            print("This file holds one controller, so there is nothing to compare.\n"
                  "Run the campaign with both, and pass a separate uncontended\n"
                  "campaign for the latency row:\n\n"
                  "  analyse_campaign.py artifacts/benchmark.json \\\n"
                  "      --latency artifacts/latency-clean.json --readme")
            return
        _, p = fisher_exact([[s["clean"], s["runs"] - s["clean"]],
                             [base["clean"], base["runs"] - base["clean"]]])
        print("## Results\n")
        print(f"Ten scenarios x {len(data.get('seeds', []))} seeds x two controllers, "
              f"on identical seeds and identical sensor noise "
              f"({len(rows)} runs). Everything below comes out of\n"
              "`scripts/benchmark.py` and is summarised by "
              "`scripts/analyse_campaign.py`; nothing here is typed in by hand.\n")
        print("| | SARATHI | Lane-following baseline |")
        print("|---|---|---|")
        print(f"| Runs collision-free | {s['clean']} / {s['runs']} "
              f"({s['clean']/s['runs']:.0%}) | {base['clean']} / {base['runs']} "
              f"({base['clean']/base['runs']:.0%}) |")
        print(f"| 95% CI on that | [{s['clean_ci'][0]:.0%}, {s['clean_ci'][1]:.0%}] | "
              f"[{base['clean_ci'][0]:.0%}, {base['clean_ci'][1]:.0%}] |")
        print(f"| Mean route progress | **{s['progress']:.1%}** | {base['progress']:.1%} |")
        print(f"| Distance covered | {s['km']:.0f} km | {base['km']:.0f} km |")
        print(f"| Contacts per 100 km | {s['per_100km']:.1f} | {base['per_100km']:.1f} |")
        print(f"| ...of which at fault | {s['at_fault_per_100km']:.1f} | "
              f"{base['at_fault_per_100km']:.1f} |")
        ls = lat.get("sarathi", s)
        print(f"| Routes completed | {s['completed']} / {s['runs']} | "
              f"{base['completed']} / {base['runs']} |")
        print(f"| Replan latency, median p95 | {ls['p95_median']:.0f} ms | - |")
        print()
        print(f"The safety difference between the two is **not statistically "
              f"significant** (Fisher exact, p = {p:.2f}): at this sample size the "
              f"campaign cannot resolve it, and neither controller can be claimed "
              f"safer than the other on this evidence."
              if p >= 0.05 else
              f"The safety difference is significant (Fisher exact, p = {p:.3f}).")
        print()
        print("Per-run collision counts flatter whichever controller moves less, "
              "so the per-kilometre row is the one to read: the baseline earns part "
              "of its clean record by covering less ground.")


if __name__ == "__main__":
    main()
