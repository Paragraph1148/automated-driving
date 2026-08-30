"""Build a self-contained replay page from a recorded run.

The published page must stand alone: the artifact sandbox blocks fetch and XHR
entirely, so the run data is embedded rather than loaded. The template is the
same one the live server renders, so the shared page and the demo cannot drift.

    sarathi replay artifacts/runs/village.json -o artifacts/mission-control.html
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .paths import viewer_template

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

    html = viewer_template()
    if PLACEHOLDER not in html:
        raise SystemExit(f"viewer template has no {PLACEHOLDER} placeholder")
    # Split the closing tag so the JSON can never terminate the script element.
    payload = json.dumps(run, separators=(",", ":")).replace("</", "<\\/")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html.replace(PLACEHOLDER, payload))
    return out_path


