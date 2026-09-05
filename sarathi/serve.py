"""Live interactive server.

    python -m sarathi serve                    # then open http://localhost:8420
    python -m sarathi serve --host 0.0.0.0     # reachable from off the machine

The page and the telemetry socket share one port: the browser asks for ``/`` and
gets Mission Control, then upgrades ``/ws`` on the same origin. That is one
listener to expose, one certificate to terminate and one ``proxy_pass`` to
write, which is what makes the demo deployable behind Caddy, an nginx or a
Cloudflare tunnel without a second hostname. See docs/05-hosting.md.

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
from http import HTTPStatus

from .core.types import AgentClass
from .paths import scenario_dir, viewer_template
from .tuning import apply as apply_tuning, read_all as read_tuning, schema
from .planning.sarathi import SarathiController
from .sim.simulator import Simulator
from .sim.snapshot import planner_snapshot
from .world.scenario import load_scenario



class LiveSession:
    """One running simulation, driven by the browser."""

    def __init__(self, scenario: str, chaos: float | None = None,
                 seed: int | None = None, scenarios: str | None = None):
        self.scenario_dir = scenario_dir(scenarios)
        self.lock = threading.Lock()
        #: Parameters the operator has changed, kept across scenario switches so
        #: a judge does not lose their tuning every time they change road.
        self.overrides: dict[str, float | bool] = {}
        self.paused = False
        self.rate = 1.0
        self.load(scenario, chaos, seed)

    def load(self, scenario: str, chaos: float | None = None,
             seed: int | None = None) -> None:
        path = self.scenario_dir / f"{scenario}.yaml"
        if not path.exists():
            raise FileNotFoundError(path)
        sc = load_scenario(path, chaos=chaos, seed=seed)
        sc.duration = 1e9          # a live session runs until someone stops it
        with self.lock:
            self.scenario_name = scenario
            self.chaos = sc.chaos
            self.seed = sc.seed
            self.sim = Simulator(sc, SarathiController(), record=False, live=True)
            self.scene = _scene_payload(self.sim.corridor)
            self.outcome = ""
        self._reapply_overrides()

    def _reapply_overrides(self) -> None:
        for key, value in list(self.overrides.items()):
            try:
                apply_tuning(self.sim.controller, key, value)
            except KeyError:
                self.overrides.pop(key, None)

    def tune(self, key: str, value) -> float | bool:
        with self.lock:
            coerced = apply_tuning(self.sim.controller, key, value)
        self.overrides[key] = coerced
        return coerced

    def reset_tuning(self) -> dict:
        self.overrides.clear()
        with self.lock:
            fresh = SarathiController()
            self.sim.controller.behaviour.cfg = fresh.behaviour.cfg
            self.sim.controller.lattice.cfg = fresh.lattice.cfg
            self.sim.controller.risk_cfg = fresh.risk_cfg
            self.sim.controller.safety.p = fresh.safety.p
            self.sim.controller.corridor_cfg = fresh.corridor_cfg
            self.sim.controller.cfg = fresh.cfg
            return read_tuning(self.sim.controller)

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

    def grab(self, agent_id: int | None = None, x: float = 0.0,
             y: float = 0.0) -> int | None:
        with self.lock:
            found = agent_id if agent_id is not None else self.sim.agent_near(x, y)
            if found is not None and self.sim.hold(int(found)):
                return int(found)
            return None

    def drag(self, agent_id: int, x: float, y: float) -> None:
        with self.lock:
            self.sim.move_held(agent_id, x, y)

    def drop(self, agent_id: int) -> None:
        with self.lock:
            self.sim.release(agent_id)

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
                # Only when true, and only for animals that have one: a cow
                # lying in the carriageway is a different hazard from one
                # walking across it, and drawn identically the operator cannot
                # tell them apart.
                if a.memory.get("resting"):
                    row["rest"] = 1
                (ego_row.update(row) if a.id == ego.id else rows.append(row))
            debug = dict(sim.controller._debug_cache) \
                if hasattr(sim.controller, "_debug_cache") else {}
            debug["replan_ms"] = round(sim.last_replan_ms, 1)
            payload = {"t": round(sim.t, 2), "ego": ego_row, "agents": rows,
                       "debug": debug}
            payload.update(planner_snapshot(sim.controller, ego))
            payload["events"] = list(sim.events)
            payload["held"] = sorted(sim.held)
            payload["paused"] = self.paused
            return payload

    def meta(self) -> dict:
        with self.lock:
            return {"mode": "live", "scenario": self.scenario_name,
                    "chaos": self.chaos, "scene": self.scene,
                    "scenarios": sorted(p.stem for p in
                                        self.scenario_dir.glob("*.yaml")),
                    "tunables": schema(),
                    "values": read_tuning(self.sim.controller)}


def _scene_payload(corridor) -> dict:
    from .sim.recorder import Recorder
    return Recorder._describe_scene(corridor)




class LiveServer:
    """One simulation, one clock, however many browsers are watching.

    Every viewer drives and observes the *same* world. That is partly the point
    of the demo — two people can interfere with one scene at once — but it is
    also the only affordable arrangement. Stepping the stack costs 30-40 ms of a
    single core per 50 ms tick and the work is Python-level, so the GIL pins it
    to one thread: measured on four threads, a second concurrent session does
    not halve the frame rate, it *thirds* it. A session per viewer would take a
    free-tier VM down at the second visitor. One session broadcast to everyone
    costs one core no matter how many are connected, and an extra spectator
    costs only their ~125 KiB/s of frames.

    The pump therefore belongs to the server, not to the connection. Attaching
    it to the connection — as this did — meant N browsers stepped the world N
    times per wall second, so the simulation ran at N x speed and the vehicle
    appeared to teleport as soon as a second person opened the page.
    """

    def __init__(self, session: LiveSession, keep_warm: bool = False):
        self.session = session
        self.keep_warm = keep_warm
        self.clients: set = set()
        self._pump: asyncio.Task | None = None

    def start(self) -> None:
        """Begin stepping before anyone has connected, if asked to stay warm."""
        if self.keep_warm and self._pump is None:
            self._pump = asyncio.create_task(self._drive())

    def attach(self, websocket) -> None:
        self.clients.add(websocket)
        if self._pump is None:
            self._pump = asyncio.create_task(self._drive())

    def detach(self, websocket) -> None:
        self.clients.discard(websocket)
        if not self.clients and self._pump is not None and not self.keep_warm:
            # Nobody is watching, so stop burning the core: on a laptop, or
            # anywhere CPU is metered, an idle demo pinning a core looks
            # identical to a runaway one.
            self._pump.cancel()
            self._pump = None

    async def _drive(self) -> None:
        """Step the simulation on a wall clock and stream every other tick."""
        dt = self.session.sim.dt
        tick = 0
        next_at = time.perf_counter()
        while True:
            self.session.step()
            if self.keep_warm and self.session.sim.finished:
                # A warm world that has driven its route to the end stops being
                # a warm world. Put the vehicle back on the road and let it run,
                # so the first visitor arrives at something already moving.
                self.session.restart_ego()
            tick += 1
            if tick % 2 == 0:
                await self.broadcast(self.session.frame())
            next_at += dt / max(self.session.rate, 0.05)
            delay = next_at - time.perf_counter()
            if delay > 0:
                await asyncio.sleep(delay)
            else:
                # Behind real time — on a slower core this is the normal state.
                # Re-anchor rather than trying to catch up, so the world runs in
                # slow motion instead of spiralling into a burst of steps.
                next_at = time.perf_counter()
                await asyncio.sleep(0)

    async def broadcast(self, payload: dict) -> None:
        """Serialise once, send to everyone: the frame is ~13 KiB of JSON."""
        if not self.clients:
            return
        payload["viewers"] = len(self.clients)
        raw = json.dumps(payload)
        await asyncio.gather(*(self._send(ws, raw) for ws in tuple(self.clients)),
                             return_exceptions=True)

    async def _send(self, websocket, raw: str) -> None:
        try:
            await websocket.send(raw)
        except Exception:
            # Drop the dead socket but leave the pump alone; the connection's
            # own handler runs detach() and decides whether anyone is left.
            self.clients.discard(websocket)


async def _handle(server: LiveServer, websocket) -> None:
    session = server.session
    await websocket.send(json.dumps({"meta": session.meta()}))
    server.attach(websocket)
    try:
        async for raw in websocket:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            try:
                await _command(server, session, websocket, msg)
            except Exception as exc:
                # A public demo takes messages from whoever opens the page, so a
                # malformed one must cost that command and nothing else. Before
                # this, an unknown agent class or a missing scenario closed the
                # socket with a 1011 and every viewer's page went dark. Reported
                # rather than swallowed, so journalctl still shows real bugs.
                print(f"  ignored {msg.get('cmd')!r}: "
                      f"{type(exc).__name__}: {exc}", flush=True)
    finally:
        server.detach(websocket)


async def _command(server: LiveServer, session: LiveSession, websocket,
                   msg: dict) -> None:
    """Apply one browser command to the shared world."""
    cmd = msg.get("cmd")
    if cmd == "place":
        session.place(msg["cls"], msg["x"], msg["y"],
                      msg.get("policy"), msg.get("speed"))
    elif cmd == "remove":
        session.remove(msg["x"], msg["y"])
    elif cmd == "grab":
        found = session.grab(msg.get("id"), msg.get("x", 0.0), msg.get("y", 0.0))
        await websocket.send(json.dumps({"grabbed": found}))
    elif cmd == "drag":
        session.drag(int(msg["id"]), msg["x"], msg["y"])
    elif cmd == "drop":
        session.drop(int(msg["id"]))
    elif cmd == "pause":
        session.paused = bool(msg.get("value", True))
    elif cmd == "rate":
        session.rate = float(msg.get("value", 1.0))
    elif cmd == "restart_ego":
        session.restart_ego()
    elif cmd == "set":
        value = session.tune(msg["key"], msg["value"])
        await websocket.send(json.dumps(
            {"tuned": {"key": msg["key"], "value": value}}))
    elif cmd == "reset_tuning":
        await server.broadcast({"values": session.reset_tuning()})
    elif cmd == "load":
        # Everyone is in this world, so everyone is told it changed.
        session.load(msg["scenario"], msg.get("chaos"), msg.get("seed"))
        await server.broadcast({"meta": session.meta()})


def _live_page() -> bytes:
    """Render the shared viewer template in live mode.

    The same template serves the recorded artifact and this page; only the
    injected payload differs, so the demo a judge drives and the page shared
    afterwards cannot drift apart. Rendered in memory rather than written to
    disk, because an installed package's directory may not be writable.

    The viewer derives its own socket URL from ``location``, so the page carries
    no host or port and is byte-identical whether it is served from a laptop on
    :8420 or from behind TLS on someone else's domain.
    """
    payload = json.dumps({"mode": "live"})
    return viewer_template().replace("__RUN_DATA__", payload).encode("utf-8")


def _http_routes(page: bytes):
    """Answer plain HTTP on the socket that also carries the telemetry.

    Returning ``None`` lets the WebSocket handshake proceed; returning a
    response serves the request and closes. The explicit charset is not
    optional: without it the browser falls back to latin-1 and every em dash and
    infinity sign in the telemetry renders as mojibake.
    """
    from websockets.datastructures import Headers
    from websockets.http11 import Response

    def process_request(connection, request):
        path = request.path.split("?", 1)[0]
        if path == "/ws":
            return None
        if path == "/healthz":
            # A body a load balancer, a systemd healthcheck or an uptime pinger
            # can read without opening a socket and pinning the core.
            return connection.respond(HTTPStatus.OK, "ok\n")
        if path in ("/", "/index.html", "/live.html"):
            return Response(200, "OK", Headers({
                "Content-Type": "text/html; charset=utf-8",
                "Content-Length": str(len(page)),
                "Cache-Control": "no-store"}), page)
        return connection.respond(HTTPStatus.NOT_FOUND, "not found\n")

    return process_request


def run(scenario: str = "highway_merge_slow", chaos: float | None = None,
        seed: int | None = None, port: int = 8420,
        scenarios: str | None = None, host: str = "127.0.0.1",
        keep_warm: bool = False) -> None:
    from websockets.asyncio.server import serve

    session = LiveSession(scenario, chaos, seed, scenarios)
    server = LiveServer(session, keep_warm=keep_warm)

    shown = "localhost" if host in ("127.0.0.1", "0.0.0.0", "::") else host
    print(f"\n  SARATHI live  ->  http://{shown}:{port}")
    print(f"  scenario: {scenario}   chaos: {session.chaos:.2f}")
    if host == "0.0.0.0":
        print("  listening on every interface")
    if keep_warm:
        print("  keeping the world running with nobody connected")
    print()

    async def main():
        async def handler(websocket):
            await _handle(server, websocket)

        server.start()
        async with serve(handler, host, port, max_size=None,
                         process_request=_http_routes(_live_page())):
            await asyncio.Future()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("stopped")
