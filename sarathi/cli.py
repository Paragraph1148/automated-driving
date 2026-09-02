"""Command-line entry point.

    uv run sarathi serve                       # live interactive demo
    uv run sarathi list                        # available scenarios
    uv run sarathi run village_road_unmarked   # one headless run
    uv run sarathi run all --chaos 0.7         # every scenario, harder
    uv run sarathi replay out.json -o page.html
"""
from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path

from .paths import scenario_dir
from .planning.baseline import BaselineLaneFollower
from .planning.sarathi import SarathiController
from .sim.simulator import Simulator
from .world.scenario import load_scenario

CONTROLLERS = {
    "baseline": BaselineLaneFollower,
    "sarathi": SarathiController,
}


def build_controller(name: str):
    if name not in CONTROLLERS:
        raise SystemExit(f"unknown controller {name!r}; "
                         f"choose from {sorted(CONTROLLERS)}")
    return CONTROLLERS[name]()


def resolve_scenarios(names: list[str], directory: str | None) -> list[Path]:
    """Accept bare names, globs, explicit paths, or the word ``all``."""
    root = scenario_dir(directory)
    out: list[Path] = []
    for name in names:
        if name == "all":
            out.extend(sorted(root.glob("*.yaml")))
            continue
        candidate = root / f"{name}.yaml"
        if candidate.exists():
            out.append(candidate)
            continue
        matched = sorted(Path(p) for p in glob.glob(name))
        if matched:
            out.extend(matched)
            continue
        raise SystemExit(
            f"no scenario named {name!r} in {root}. "
            f"Known: {', '.join(sorted(p.stem for p in root.glob('*.yaml')))}")
    # De-duplicate while preserving order.
    seen, unique = set(), []
    for path in out:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def cmd_run(args: argparse.Namespace) -> int:
    paths = resolve_scenarios(args.scenarios, args.scenarios_dir)
    if not paths:
        raise SystemExit("no scenarios matched")

    failures = 0
    for path in paths:
        scenario = load_scenario(path, chaos=args.chaos, seed=args.seed)
        sim = Simulator(scenario, build_controller(args.controller),
                        record=bool(args.record), record_stride=args.record_stride,
                        record_planner=bool(args.record) and not args.no_planner_layers)
        result = sim.run(verbose=True)
        if args.record:
            out = Path(args.record)
            if len(paths) > 1 or out.is_dir():
                out = Path(args.record) / f"{scenario.name}.json"
            saved = result.recorder.save(out)
            print(f"    recorded -> {saved}")
        if result.metrics.collision or result.metrics.left_corridor:
            failures += 1
    print(f"\n{len(paths) - failures}/{len(paths)} runs without collision or off-road")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .serve import run
    run(args.scenario, args.chaos, args.seed, args.port, args.scenarios_dir,
        host=args.host, keep_warm=args.keep_warm)
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    from .replay import build
    out = build(Path(args.run), Path(args.out), args.chaos, args.outcome)
    print(f"{out}  ({out.stat().st_size / 1e6:.2f} MB)")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    root = scenario_dir(args.scenarios_dir)
    print(f"scenarios in {root}\n")
    for path in sorted(root.glob("*.yaml")):
        sc = load_scenario(path)
        tags = ",".join(sc.tags)
        print(f"{path.stem:<32s} chaos={sc.chaos:.2f} "
              f"{sc.duration:5.0f}s  [{tags}]")
        print(f"    {sc.description.strip().splitlines()[0]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sarathi",
        description="SARATHI - adaptive path planning for unstructured Indian roads")
    parser.add_argument("--scenarios-dir", default=None,
                        help="directory of scenario YAML files "
                             "(default: ./scenarios, else the bundled set)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="run one or more scenarios")
    run.add_argument("scenarios", nargs="+",
                     help="scenario names, 'all', or paths")
    run.add_argument("--controller", default="sarathi")
    run.add_argument("--chaos", type=float, default=None,
                     help="override the scenario chaos level, 0..1")
    run.add_argument("--seed", type=int, default=None)
    run.add_argument("--record", default=None,
                     help="write a run recording (file, or directory for many)")
    run.add_argument("--record-stride", type=int, default=2)
    run.add_argument("--no-planner-layers", action="store_true",
                     help="record only poses, omitting risk field and candidate fan")
    run.set_defaults(func=cmd_run)

    srv = sub.add_parser("serve", help="run the live interactive demo")
    srv.add_argument("--scenario", default="village_road_unmarked")
    srv.add_argument("--chaos", type=float, default=None)
    srv.add_argument("--seed", type=int, default=None)
    srv.add_argument("--port", type=int, default=8420,
                     help="page and telemetry socket share this one port")
    srv.add_argument("--host", default="127.0.0.1",
                     help="interface to bind; 0.0.0.0 to accept traffic from "
                          "off the machine (see docs/05-hosting.md)")
    srv.add_argument("--keep-warm", action="store_true",
                     help="keep the world running with nobody connected, so "
                          "the first visitor arrives at a moving scene. Also "
                          "what stops Oracle reclaiming an Always Free VM it "
                          "has decided is idle (docs/05-hosting.md)")
    srv.set_defaults(func=cmd_serve)

    lst = sub.add_parser("list", help="list available scenarios")
    lst.set_defaults(func=cmd_list)

    rep = sub.add_parser("replay", help="build a shareable replay page")
    rep.add_argument("run")
    rep.add_argument("-o", "--out", default="artifacts/mission-control.html")
    rep.add_argument("--chaos", type=float, default=None)
    rep.add_argument("--outcome", default=None)
    rep.set_defaults(func=cmd_replay)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
