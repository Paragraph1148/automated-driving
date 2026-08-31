"""Capture figures from the live demo, waiting for a state worth photographing.

Screenshots of a live system are only useful if the instant they are taken shows
the system working. This starts the real server, drives a real browser, and waits
until the telemetry satisfies a condition given on the command line - a behaviour
we want to show, positive path clearance, enough elapsed time for the plan to
settle - before it shoots. That way a figure in the deck is a moment we chose,
not a moment we happened to catch.

    uv run python scripts/capture.py --scenario bus_stop_overtake \
        --behaviour OVERTAKE --min-clearance 0.15 --out artifacts/fig-overtake.png
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def wait_for_state(page, behaviours, min_clearance, min_time, timeout):
    """Poll the console until the run looks the way we want to show it."""
    deadline = time.time() + timeout
    best = None
    while time.time() < deadline:
        try:
            state = page.evaluate(
                """() => ({
                    behaviour: (document.querySelector('#m-behaviour')||{}).textContent,
                    clearance: (document.querySelector('#m-clr')||{}).textContent,
                    ttc: (document.querySelector('#m-ttc')||{}).textContent,
                    speed: (document.querySelector('#m-speed')||{}).textContent,
                    latency: (document.querySelector('#m-lat')||{}).textContent,
                    clock: (document.querySelector('#live-clock')||{}).textContent,
                })""")
        except Exception:
            time.sleep(0.2)
            continue
        try:
            clearance = float(str(state["clearance"]).replace("−", "-"))
        except (TypeError, ValueError):
            clearance = -99.0
        try:
            elapsed = float(str(state["clock"]).split()[0])
        except (TypeError, ValueError, IndexError):
            elapsed = 0.0
        name = (state["behaviour"] or "").strip().upper()
        ok = (elapsed >= min_time
              and clearance >= min_clearance
              and (not behaviours or name in behaviours)
              and str(state["latency"]).strip() not in ("", "—"))
        best = state | {"clearance": clearance, "elapsed": elapsed}
        if ok:
            return True, best
        time.sleep(0.25)
    return False, best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="bus_stop_overtake")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--chaos", type=float, default=None)
    ap.add_argument("--port", type=int, default=8520)
    ap.add_argument("--behaviour", default="", help="comma-separated, any of them")
    ap.add_argument("--min-clearance", type=float, default=0.15)
    ap.add_argument("--min-time", type=float, default=6.0)
    ap.add_argument("--timeout", type=float, default=90.0)
    ap.add_argument("--width", type=int, default=1600)
    ap.add_argument("--height", type=int, default=1000)
    ap.add_argument("--scale", type=float, default=2.0)
    ap.add_argument("--theme", default="light", choices=("light", "dark"),
                    help="the page follows the browser's colour scheme; a light "
                         "capture sits better on a white slide")
    ap.add_argument("--place", default="",
                    help="palette class to drop before shooting, e.g. COW")
    ap.add_argument("--place-at", default="0.62,0.5",
                    help="where on the map to drop it, as fractions of the stage")
    ap.add_argument("--stage-only", action="store_true",
                    help="also write a <name>-bev.png cropped to the map")
    ap.add_argument("-o", "--out", default="artifacts/capture.png")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    cmd = [sys.executable, "-m", "sarathi.cli", "serve",
           "--scenario", args.scenario, "--port", str(args.port)]
    if args.seed is not None:
        cmd += ["--seed", str(args.seed)]
    if args.chaos is not None:
        cmd += ["--chaos", str(args.chaos)]
    server = subprocess.Popen(cmd, cwd=REPO, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True,
                              start_new_session=True)
    try:
        time.sleep(3.0)
        with sync_playwright() as pw:
            # The sandbox ships one Chromium build; the pinned playwright wheel
            # looks for a different revision and would otherwise ask to download.
            chrome = "/opt/pw-browsers/chromium"
            browser = pw.chromium.launch(
                executable_path=chrome if os.path.exists(chrome) else None)
            context = browser.new_context(
                viewport={"width": args.width, "height": args.height},
                device_scale_factor=args.scale, color_scheme=args.theme)
            page = context.new_page()
            page.goto(f"http://127.0.0.1:{args.port}/", wait_until="networkidle")
            wants = {b.strip().upper() for b in args.behaviour.split(",") if b.strip()}
            ok, state = wait_for_state(page, wants, args.min_clearance,
                                       args.min_time, args.timeout)
            if args.place:
                # Do exactly what a judge does: pick the class, click the road.
                page.click(f"#palette >> text={args.place.upper()}")
                stage = page.query_selector("#stage")
                bb = stage.bounding_box()
                fx, fy = (float(v) for v in args.place_at.split(","))
                page.mouse.click(bb["x"] + bb["width"] * fx,
                                 bb["y"] + bb["height"] * fy)
                page.wait_for_timeout(1200)
            out = REPO / args.out
            out.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(out))
            if args.stage_only:
                stage = page.query_selector("#stage")
                if stage is not None:
                    stage.screenshot(path=str(out.with_name(out.stem + "-bev.png")))
            browser.close()
        print(("captured " if ok else "TIMED OUT, captured anyway ") + str(out))
        print(f"  state: {state}")
        return 0 if ok else 1
    finally:
        os.killpg(os.getpgid(server.pid), signal.SIGTERM)


if __name__ == "__main__":
    sys.exit(main())
