"""Inject a recorded run into the Mission Control template.

The published page must be self-contained: the artifact sandbox blocks fetch and
XHR entirely, so the run data is embedded rather than loaded. Keeping the template
and the data separate here means the viewer stays reviewable as source while the
built page stays a single file.

    python viz/build.py artifacts/runs/village.json -o artifacts/mission-control.html
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

TEMPLATE = Path(__file__).with_name("mission_control.html")
PLACEHOLDER = "__RUN_DATA__"


def summarise(run: dict) -> dict:
    """Attach the run-level facts the header and telemetry rail display."""
    frames = run.get("frames", [])
    latencies = [f["debug"].get("replan_ms") for f in frames
                 if isinstance(f.get("debug"), dict)]
    latencies = sorted(v for v in latencies if isinstance(v, (int, float)))
    if latencies:
        run["latency_p95"] = latencies[int(0.95 * (len(latencies) - 1))]
    run.setdefault("chaos", run.get("chaos"))
    return run


def build(run_path: Path, out_path: Path, chaos: float | None = None,
          outcome: str | None = None) -> Path:
    run = json.loads(run_path.read_text())
    if chaos is not None:
        run["chaos"] = chaos
    if outcome is not None:
        run["outcome"] = outcome
    run = summarise(run)

    html = TEMPLATE.read_text()
    if PLACEHOLDER not in html:
        raise SystemExit(f"{TEMPLATE} has no {PLACEHOLDER} placeholder")
    # Split the closing tag so the JSON can never terminate the script element.
    payload = json.dumps(run, separators=(",", ":")).replace("</", "<\\/")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html.replace(PLACEHOLDER, payload))
    return out_path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("-o", "--out", default="artifacts/mission-control.html")
    ap.add_argument("--chaos", type=float, default=None)
    ap.add_argument("--outcome", default=None)
    args = ap.parse_args()
    out = build(Path(args.run), Path(args.out), args.chaos, args.outcome)
    print(f"{out}  ({out.stat().st_size/1e6:.2f} MB)")


if __name__ == "__main__":
    main()
