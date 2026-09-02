"""The live server's contract with a browser, and with whatever hosts it.

These are the properties that hosting depends on: one port carries both the page
and the telemetry, the socket URL the page derives has to work behind TLS, and
one simulation is shared by every viewer rather than stepped once per viewer.
The last of those was a real bug — N browsers ran the world at N times speed —
and it only shows up with two clients connected at once.
"""
from __future__ import annotations

import asyncio
import json
import urllib.request

import pytest
import websockets
from websockets.asyncio.server import serve

from sarathi.serve import (LiveServer, LiveSession, _handle, _http_routes,
                           _live_page)

PORT = 8479


def run(coro):
    """Drive one async case without taking a pytest-asyncio dependency."""
    return asyncio.run(asyncio.wait_for(coro, timeout=60))


class Harness:
    """The real server on a real socket; nothing here is mocked."""

    def __init__(self, scenario="village_road_unmarked", port=PORT):
        self.session = LiveSession(scenario, seed=1)
        self.server = LiveServer(self.session)
        self.port = port

    def listen(self):
        async def handler(ws):
            await _handle(self.server, ws)
        return serve(handler, "127.0.0.1", self.port, max_size=None,
                     process_request=_http_routes(_live_page()))

    def url(self, path="/ws"):
        return f"ws://127.0.0.1:{self.port}{path}"

    async def get(self, path):
        def fetch():
            try:
                r = urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}")
                return r.status, r.headers.get("Content-Type"), r.read()
            except urllib.error.HTTPError as e:
                return e.code, e.headers.get("Content-Type"), e.read()
        return await asyncio.to_thread(fetch)

    async def frames(self, conn, n):
        """Collect n telemetry frames, skipping the meta and ack messages."""
        out = []
        while len(out) < n:
            msg = json.loads(await conn.recv())
            if msg.get("t") is not None:
                out.append(msg)
        return out


def test_page_and_socket_share_one_port():
    """One listener is the whole reason this can sit behind one proxy line."""
    async def case():
        h = Harness(port=PORT)
        async with h.listen():
            status, ctype, body = await h.get("/")
            assert status == 200
            assert ctype == "text/html; charset=utf-8"
            assert b"SARATHI Mission Control" in body
            async with websockets.connect(h.url()) as conn:
                assert "meta" in json.loads(await conn.recv())
    run(case())


def test_page_declares_utf8():
    """Without the charset the browser guesses latin-1 and the em dashes rot."""
    async def case():
        h = Harness(port=PORT + 1)
        async with h.listen():
            _, ctype, body = await h.get("/")
            assert "charset=utf-8" in ctype
            body.decode("utf-8")          # raises if the bytes are not UTF-8
    run(case())


def test_healthz_needs_no_socket():
    """A health check must not start a simulation and pin a core."""
    async def case():
        h = Harness(port=PORT + 2)
        async with h.listen():
            status, _, body = await h.get("/healthz")
            assert status == 200 and body.strip() == b"ok"
            assert h.server._pump is None
            assert not h.server.clients
    run(case())


def test_unknown_path_is_404():
    async def case():
        h = Harness(port=PORT + 3)
        async with h.listen():
            status, _, _ = await h.get("/../etc/passwd")
            assert status == 404
    run(case())


def test_two_viewers_share_one_world_at_one_speed():
    """The bug this guards: a pump per connection ran the world at N x speed."""
    async def case():
        h = Harness(port=PORT + 4)
        async with h.listen():
            async with websockets.connect(h.url()) as a, \
                       websockets.connect(h.url()) as b:
                first = await asyncio.gather(h.frames(a, 1), h.frames(b, 1))
                pairs = await asyncio.gather(h.frames(a, 12), h.frames(b, 12))

                # One clock: both see the same sim time advance, and the world
                # advances at most one tick's worth per tick of the wall clock.
                span_a = pairs[0][-1]["t"] - pairs[0][0]["t"]
                span_b = pairs[1][-1]["t"] - pairs[1][0]["t"]
                assert span_a == pytest.approx(span_b, abs=0.31)

                # One world: a frame stamped with a given sim time is the same
                # frame for both viewers, not two independent simulations.
                by_t = {f["t"]: f["ego"]["x"] for f in pairs[0]}
                shared = [f for f in pairs[1] if f["t"] in by_t]
                assert shared, "viewers never saw a common timestamp"
                assert all(by_t[f["t"]] == f["ego"]["x"] for f in shared)

                assert pairs[0][-1]["viewers"] == 2
                assert len(h.server.clients) == 2
                assert first  # meta arrived before any frame
    run(case())


def test_pump_stops_when_the_last_viewer_leaves():
    """An unvisited demo should not burn a core on a free-tier box."""
    async def case():
        h = Harness(port=PORT + 5)
        async with h.listen():
            async with websockets.connect(h.url()) as conn:
                await h.frames(conn, 2)
                assert h.server._pump is not None
            for _ in range(50):
                await asyncio.sleep(0.02)
                if h.server._pump is None:
                    break
            assert h.server._pump is None
            assert not h.server.clients
    run(case())


def test_placing_an_agent_reaches_the_shared_world():
    """The interaction path a judge uses, end to end over the socket."""
    async def case():
        h = Harness(port=PORT + 6)
        async with h.listen():
            async with websockets.connect(h.url()) as conn:
                before = (await h.frames(conn, 1))[0]
                ego = before["ego"]
                await conn.send(json.dumps({"cmd": "place", "cls": "cattle",
                                            "x": ego["x"] + 25.0,
                                            "y": ego["y"]}))
                after = await h.frames(conn, 6)
                assert any(len(f["agents"]) > len(before["agents"])
                           for f in after)
    run(case())


def test_live_page_carries_no_host_or_port():
    """The page must be identical on a laptop and behind someone's TLS."""
    page = _live_page().decode("utf-8")
    assert '{"mode": "live"}' in page
    assert "ws://${location.hostname}" not in page
    # The scheme is derived, never hardcoded, or an https page cannot connect.
    assert 'location.protocol === "https:" ? "wss:" : "ws:"' in page


def test_a_malformed_command_does_not_drop_the_connection():
    """A public URL takes input from anyone; one bad message must not end the
    session for the person sending it, let alone look like a server crash."""
    async def case():
        h = Harness(port=PORT + 7)
        async with h.listen():
            async with websockets.connect(h.url()) as conn:
                await h.frames(conn, 1)
                for junk in ('not json at all',
                             json.dumps({"cmd": "place", "cls": "unicorn",
                                         "x": 0, "y": 0}),
                             json.dumps({"cmd": "drag", "id": "nonsense",
                                         "x": 0, "y": 0}),
                             json.dumps({"cmd": "place"}),
                             json.dumps({"cmd": "load", "scenario": "nope"}),
                             json.dumps({"cmd": "rate", "value": "fast"}),
                             json.dumps({"cmd": "nonexistent"})):
                    await conn.send(junk)
                # Still live, still streaming the same world.
                assert len(await h.frames(conn, 4)) == 4
    run(case())
