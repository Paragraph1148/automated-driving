"""Command-line entry point.

    python -m sarathi run scenarios/village_road_unmarked.yaml
    python -m sarathi run scenarios/*.yaml --chaos 0.7 --seed 5
    python -m sarathi run scenarios/market_dense_mixed.yaml --record out.json
"""
from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path

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


def cmd_run(args: argparse.Namespace) -> int:
    paths: list[str] = []
    for pattern in args.scenarios:
        matched = sorted(glob.glob(pattern))
        paths.extend(matched or [pattern])
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


def cmd_list(args: argparse.Namespace) -> int:
    for path in sorted(glob.glob("scenarios/*.yaml")):
        sc = load_scenario(path)
        tags = ",".join(sc.tags)
        print(f"{Path(path).name:<38s} chaos={sc.chaos:.2f} "
              f"{sc.duration:5.0f}s  [{tags}]")
        print(f"    {sc.description.strip().splitlines()[0]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sarathi")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="run one or more scenarios")
    run.add_argument("scenarios", nargs="+")
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

    lst = sub.add_parser("list", help="list available scenarios")
    lst.set_defaults(func=cmd_list)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
