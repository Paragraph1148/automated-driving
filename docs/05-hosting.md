# Hosting the live demo

The demo is not a website. It is a physics simulation and a full planning stack
running in real time, streaming ~13 KiB of telemetry ten times a second to a
browser that can reach back in and change the world. What it needs from a host
follows from that, and it rules most of the free tier out immediately.

## What one session actually costs

Measured with `scripts/hostcheck.py` on a 4-core x86 cloud box:

| | `village_road_unmarked` | `market_dense_mixed` |
|---|---|---|
| Tick budget at 20 Hz | 50 ms | 50 ms |
| Step, median | 31 ms | 39 ms |
| Step, p95 | 39 ms | 49 ms |
| Frame serialise | 4 ms | 4 ms |
| Outbound per viewer | 128 KiB/s | 123 KiB/s |

Three consequences, and everything else on this page is downstream of them.

**A world costs most of a core, permanently.** Not a burst at page load — a
continuous 60-80 % of one core for as long as someone is watching. Anything
billed by CPU-second, or that throttles CPU between requests, is being asked for
the one resource it is stingiest with.

**Threads buy nothing.** The stack is Python-level work, so the GIL pins it to
one thread. Stepping *n* sessions on *n* threads in one process, measured:

| Sessions in one process | 1 | 2 | 3 | 4 |
|---|---|---|---|---|
| Mean step | 35 ms | 119 ms | 198 ms | 292 ms |

A second concurrent session does not halve the frame rate, it thirds it. So
concurrency means **processes**, one per core, and a 4-core VM hosts three
simultaneous *worlds* — not three viewers.

**Viewers, though, are nearly free.** One simulation broadcasts to everyone
connected: the frame is serialised once and sent *n* times. An extra spectator
costs 128 KiB/s and no CPU at all. Thirty people watching one world is a
bandwidth question (~14 GB/hour, against Oracle's 10 TB/month) and never a CPU
one. That is why `LiveServer` runs one pump for the server rather than one per
connection — the old arrangement stepped the world once per connected browser,
so the vehicle visibly ran at double speed the moment a second judge opened the
page.

## The recommendation: Oracle Cloud, always-free Ampere A1

**VM.Standard.A1.Flex, 4 OCPU / 24 GB, Ubuntu.** Free indefinitely, not a trial,
and 10 TB/month of egress — about 22,000 viewer-hours at our frame size. It is a
persistent VM with real cores that stay yours between requests, which is exactly
and only what this workload wants.

```bash
scp deploy/setup-oracle.sh ubuntu@<ip>:
ssh ubuntu@<ip> 'sudo bash setup-oracle.sh demo.example.org'
```

That installs uv, syncs the locked dependencies, runs `hostcheck.py` so you find
out *now* whether the box holds 20 Hz, installs the systemd unit, puts Caddy in
front with an automatic certificate, and opens the host firewall.

Two things reliably go wrong, neither of them with the code:

- **"Out of host capacity."** The free ARM shape is heavily oversubscribed in
  popular regions. Retry on a schedule, or try a different availability domain;
  a home-region change is a one-way door, so decide before you ask.
- **The port is open in the console and still dead.** Oracle's Ubuntu images
  ship an iptables `REJECT` for everything but SSH, *underneath* the VCN
  security list, and neither layer reports the other. `setup-oracle.sh` fixes
  the host side; the VCN ingress rule for 80/443 you still have to add in
  Networking → Virtual Cloud Networks → Security Lists.

An Ampere core is slower per thread than the x86 box the table above was
measured on, so expect the step to land nearer the 50 ms budget — possibly over
it on the dense market scene. The demo degrades to slow motion rather than
breaking, and `hostcheck.py` tells you which side of the line you are on. If the
default scene is tight, serve a lighter one: `--scenario cattle_crossing`.

## Why not Lambda

Not stubbornness about serverless — the shape genuinely does not fit, in three
independent ways:

1. **Lambda cannot hold the socket.** The demo is one WebSocket open for
   minutes. Lambda would need API Gateway's WebSocket API, where the *gateway*
   holds the connection and invokes a function per message. At 20 Hz that is 20
   invocations per second per viewer.
2. **The state has nowhere to live.** Between ticks, a session is Kalman filter
   covariances, a risk field, per-agent RNG streams and numpy arrays. A
   stateless function would have to serialise and restore all of it every 50 ms.
   That round trip alone exceeds the tick budget, before any planning happens.
3. **The free tier is not free here.** 1M requests/month sounds generous until
   you divide by 20/s: about 14 hours of a *single* viewer. API Gateway
   WebSockets are not in the perpetual free tier at all.

Porting this to Lambda is not a deployment change, it is a rewrite into a
different architecture — one that would be slower and cost more than a VM you
can have for nothing. The same reasoning rules out Cloudflare Workers and any
other per-invocation runtime.

## The alternatives, ranked

| Host | Free allowance | Verdict |
|---|---|---|
| **Oracle Ampere A1** | 4 OCPU / 24 GB, 10 TB egress, indefinite | **Recommended.** Persistent cores, enough headroom for three worlds. |
| **Hugging Face Spaces** | 2 vCPU / 16 GB, Docker, HTTPS included | **Best fallback.** Push `deploy/Dockerfile`, get a public TLS URL, no VM to administer. Sleeps after ~48 h idle; wakes on request. One world, since two vCPU. |
| **Google Cloud Run** | 180k vCPU-s/month, WebSockets to 60 min | Viable, ~50 vCPU-hours/month ≈ 60 hours of demo. Bills for CPU for the whole connection, and the 60-minute cap disconnects a long session. |
| **Fly.io** | No usable perpetual free tier now | Scale-to-zero would suspend the simulation anyway. |
| **Render** | — | Ruled out by you. Free web services sleep and the restart is slow. |
| **AWS Lambda** | — | See above. Wrong shape at any price. |

For demo day specifically, Oracle plus Hugging Face Spaces as a standing backup
is the combination worth having: two URLs, no shared failure mode, both free.

## Running it

**One port, both protocols.** `sarathi serve` answers `/` with Mission Control
and upgrades `/ws` on the same port. That is deliberate: the page derives its own
socket URL from `location`, so there is one listener to expose, one certificate,
one `reverse_proxy` line, and no way for the page and the socket to disagree
about where they are. It is also what makes TLS possible at all — an `https:`
page may not open a plaintext `ws://` socket, and the viewer used to hardcode
one.

```bash
sarathi serve                                  # laptop, loopback only
sarathi serve --host 0.0.0.0 --port 8420       # reachable from off the box
curl -s localhost:8420/healthz                 # no socket, no simulation started
```

`--host` defaults to loopback. Behind Caddy or a tunnel that is what you want —
the proxy is the only thing on a public port.

**No domain?** A Cloudflare tunnel gives TLS and a public hostname without
opening any port, which side-steps the Oracle firewall layers entirely:

```bash
cloudflared tunnel --url http://localhost:8420
```

**Docker instead of systemd:**

```bash
docker build -t sarathi -f deploy/Dockerfile .
docker run --rm -p 8420:8420 sarathi
DOMAIN=demo.example.org docker compose -f deploy/compose.yaml up -d
```

The image is multi-arch by construction — the same file builds on the Ampere VM
and on an x86 laptop.

## What is deliberately not solved

- **Nothing separates viewers.** Everyone shares one world by design: two judges
  can interfere with one scene, which is the demo's whole argument. If you need
  isolated sessions, run several processes on different ports and let the proxy
  hand each visitor one — and remember each costs a core.
- **No authentication.** A public URL is a public simulation. Anyone who finds it
  can drag a cow into the road. There is nothing to protect and no data to leak;
  the only real exposure is the core it burns, which the `CPUQuota` in the unit
  file bounds.
- **The pump idles when nobody is connected**, so an unvisited demo costs no CPU.
  It also means the first visitor gets a world that has not been running.
