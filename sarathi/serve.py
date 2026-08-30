"""Live interactive server.

    python -m sarathi serve
    # then open http://localhost:8420

Runs the **real** planning stack in real time and streams it to the browser, so a
judge can drop a two-wheeler in front of the vehicle with the mouse and watch the
same code that ships in the submission perceive and react to it. Nothing is
scripted and nothing is pre-computed: the planner has no more foreknowledge of a
hand-placed obstacle than it does of anything else in the scene.

This matters more than it sounds. A recorded video invites the question "how do we
know it isn't a replay?", and there is no good answer. A judge who places the
obstacle himself has already answered it.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .core.types import AgentClass
from .planning.sarathi import SarathiController
from .sim.simulator import Simulator
from .sim.snapshot import planner_snapshot
from .world.scenario import load_scenario

SCENARIO_DIR = Path("scenarios")
VIZ_DIR = Path(__file__).resolve().parent.parent / "viz"


class LiveSession:
    """One running simulation, driven by the browser."""

    def __init__(self, scenario: str, chaos: float | None = None,
                 seed: int | None = None):
        self.lock = threading.Lock()
        self.paused = False
        self.rate = 1.0
        self.load(scenario, chaos, seed)

    def load(self, scenario: str, chaos: float | None = None,
             seed: int | None = None) -> None:
        path = SCENARIO_DIR / f"{scenario}.yaml"
        if not path.exists():
            raise FileNotFoundError(path)
        sc = load_scenario(path, chaos=chaos, seed=seed)
        sc.duration = 1e9          # a live session runs until someone stops it
        with self.lock:
            self.scenario_name = scenario
            self.chaos = sc.chaos
            self.seed = sc.seed
            self.sim = Simulator(sc, SarathiController(), record=False)
            self.scene = _scene_payload(self.sim.corridor)
            self.outcome = ""

    def step(self) -> None:
        with self.lock:
            if self.paused or self.sim.finished:
                return
            self.sim.step()
            if self.sim.finished:
                self.outcome = self.sim.outcome

    def restart_ego(self) -> None:
        """Put the ego back at the start without rebuilding the traffic."""
        with self.lock:
            self.sim.finished = False
            self.sim.outcome = ""
            self.outcome = ""
            self.sim._add_ego()
            self.sim.controller.reset(self.sim.scenario)

    def place(self, cls: str, x: float, y: float, policy: str | None,
              speed: float | None) -> int:
        with self.lock:
            return self.sim.spawn(AgentClass(cls), x, y,
                                  policy=policy, speed=speed)

    def remove(self, x: float, y: float) -> int | None:
        with self.lock:
            return self.sim.despawn_near(x, y, radius=4.0)

    def frame(self) -> dict:
        with self.lock:
            sim = self.sim
            ego = sim.ego
            rows = []
            ego_row = {}
            for a in sim.agents.values():
                if not a.active:
                    continue
                row = {"id": a.id, "cls": a.cls.value,
                       "x": round(a.state.x, 2), "y": round(a.state.y, 2),
                       "h": round(a.state.heading, 3),
                       "v": round(a.state.speed, 2),
                       "l": round(a.params.length, 2),
                       "w": round(a.params.width, 2)}
                (ego_row.update(row) if a.id == ego.id else rows.append(row))
            payload = {"t": round(sim.t, 2), "ego": ego_row, "agents": rows,
                       "debug": dict(sim.controller._debug_cache)
                       if hasattr(sim.controller, "_debug_cache") else {}}
            payload.update(planner_snapshot(sim.controller, ego))
            payload["outcome"] = self.outcome
            payload["paused"] = self.paused
            return payload

    def meta(self) -> dict:
        with self.lock:
            return {"mode": "live", "scenario": self.scenario_name,
                    "chaos": self.chaos, "scene": self.scene,
                    "scenarios": sorted(p.stem for p in
                                        SCENARIO_DIR.glob("*.yaml"))}


def _scene_payload(corridor) -> dict:
    from .sim.recorder import Recorder
    return Recorder._describe_scene(corridor)


async def _pump(session: LiveSession, websocket) -> None:
    """Step the simulation on a wall clock and stream every other tick."""
    dt = session.sim.dt
    tick = 0
    next_at = time.perf_counter()
    while True:
        session.step()
        tick += 1
        if tick % 2 == 0:
            try:
                await websocket.send(json.dumps(session.frame()))
            except Exception:
                return
        next_at += dt / max(session.rate, 0.05)
        delay = next_at - time.perf_counter()
        if delay > 0:
            await asyncio.sleep(delay)
        else:
            next_at = time.perf_counter()
            await asyncio.sleep(0)


async def _handle(session: LiveSession, websocket) -> None:
    await websocket.send(json.dumps({"meta": session.meta()}))
    pump = asyncio.create_task(_pump(session, websocket))
    try:
        async for raw in websocket:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            cmd = msg.get("cmd")
            if cmd == "place":
                session.place(msg["cls"], msg["x"], msg["y"],
                              msg.get("policy"), msg.get("speed"))
            elif cmd == "remove":
                session.remove(msg["x"], msg["y"])
            elif cmd == "pause":
                session.paused = bool(msg.get("value", True))
            elif cmd == "rate":
                session.rate = float(msg.get("value", 1.0))
            elif cmd == "restart_ego":
                session.restart_ego()
            elif cmd == "load":
                session.load(msg["scenario"], msg.get("chaos"), msg.get("seed"))
                await websocket.send(json.dumps({"meta": session.meta()}))
    finally:
        pump.cancel()


def _write_live_page(port: int) -> None:
    """Render the shared template in live mode.

    The same file serves the recorded artifact and this page; only the injected
    payload differs. Keeping one renderer means the demo a judge drives and the
    page shared afterwards cannot drift apart.
    """
    template = (VIZ_DIR / "mission_control.html").read_text()
    payload = json.dumps({"mode": "live", "port": port})
    (VIZ_DIR / "live.html").write_text(
        template.replace("__RUN_DATA__", payload))


class _Utf8Handler(SimpleHTTPRequestHandler):
    """Static handler that declares UTF-8.

    Python's default guesses the charset from the file extension alone, so the
    browser falls back to latin-1 and every em dash and infinity sign in the page
    renders as mojibake.
    """

    def guess_type(self, path):
        base = super().guess_type(path)
        if isinstance(base, str) and base.startswith("text/"):
            return f"{base}; charset=utf-8"
        return base

    def log_message(self, *args):
        pass


def _serve_static(port: int) -> None:
    handler = partial(_Utf8Handler, directory=str(VIZ_DIR))
    ThreadingHTTPServer(("127.0.0.1", port), handler).serve_forever()


def run(scenario: str = "village_road_unmarked", chaos: float | None = None,
        seed: int | None = None, port: int = 8420) -> None:
    import websockets

    session = LiveSession(scenario, chaos, seed)
    _write_live_page(port)
    threading.Thread(target=_serve_static, args=(port,), daemon=True).start()

    print(f"\n  SARATHI live  →  http://localhost:{port}/live.html")
    print(f"  scenario: {scenario}   chaos: {session.chaos:.2f}\n")

    async def main():
        async with websockets.serve(partial(_handle, session), "127.0.0.1",
                                    port + 1, max_size=None):
            await asyncio.Future()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("stopped")
