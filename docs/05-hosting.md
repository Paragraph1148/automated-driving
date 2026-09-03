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

## Pick by what you can pay with

Every "free tier" worth having asks for a card as identity verification, Oracle
included — its always-free Ampere shape is the best fit for this workload and
you still cannot reach it without one. So the first question is not which host
is best, it is which host will let you in.

### No credit card

**A tunnel from a machine you already have.** No account, no card, no server:

```bash
./scripts/share.sh
```

That serves the demo on loopback, opens a Cloudflare quick tunnel and prints an
`https://<random>.trycloudflare.com` URL anyone can open. It works because the
page and the socket share one port and the viewer derives `wss://` from the
page's origin — one tunnel carries both, and TLS terminates at Cloudflare.

The URL is random and changes every run, and the demo lives only as long as the
terminal stays open. For a judged demo that is usually the right trade: the
laptop is in the room anyway, and a judge dragging a cow across your screen is
the whole argument. Some campus networks block the tunnel transport, so run it
once on the network you will present from — the script tells you whether the URL
is actually reachable rather than leaving you to find out live.

**GitHub Codespaces**, when you want it off your laptop. A personal account
includes 120 core-hours and 15 GB a month with no payment method on file — 60
hours of a 2-core machine, which is more demo than you will ever give — and
usage simply stops when the quota does. `.devcontainer/` is set up, so:

```bash
uv run sarathi serve
gh codespace ports visibility 8420:public -c $CODESPACE_NAME
```

Port visibility cannot be set from `devcontainer.json`; it is that command or
the Ports panel. The codespace stops after 30 minutes idle and the URL is stable
across restarts.

**Azure for Students**, if you have a college email — which, for an SIH entry,
you do. $100 of credit for 12 months, renewable while you are a student, and
explicitly no credit card. That buys a small always-on VM, which is the only
option in this section that survives you closing your laptop. Provision a B1s or
B2s running Ubuntu and `deploy/setup-oracle.sh` works on it unchanged — it is a
plain Ubuntu script, and only the firewall section is Oracle-specific.

### With a credit card

**Oracle Cloud, always-free Ampere A1** — VM.Standard.A1.Flex. A persistent VM
with real cores that stay yours between requests is exactly and only what this
workload wants, and the card is used for verification, not billing.

Size it at **2 OCPU / 12 GB**, which is the whole Always Free Ampere allowance:
Oracle halved it in July 2026, from 4 OCPU / 24 GB, without announcing it. The
allowance is metered in hours — 1,500 OCPU-hours and 9,000 GB-hours a month —
and a 2 OCPU / 12 GB instance left running consumes 1,488 and 8,928 of them in a
31-day month. It fits, with about 1% to spare, and the margin is consumed by
*allocated* time rather than busy time: a second instance spun up "just to test"
for an afternoon puts you over. Run one instance and nothing else.

That is one core for the world and one for the operating system and the proxy,
which is exactly why one shared simulation broadcast to every viewer is not an
optimisation here but the thing that makes it possible at all. 10 TB/month of
egress is about 22,000 viewer-hours at our frame size — not the binding
constraint.

**It will be deleted if it looks idle.** Oracle reclaims an Always Free compute
instance whose 95th-percentile CPU, network *and* memory all sit under 20% over
a seven-day window. An unvisited demo is precisely that shape, because the pump
stops when the last viewer leaves — so the very thing that makes the demo polite
on a laptop is what would silently destroy the VM a week before you need it.
`deploy/sarathi.service` therefore runs it with `--keep-warm`, which keeps the
world stepping with nobody connected and restarts the route when the vehicle
finishes it. The instance stays busy because the demo is actually running, and
the first visitor arrives at a scene already in motion rather than a still one.

Choose **Canonical Ubuntu 24.04 LTS** for the image, not the newest release on
the list: Caddy's apt repository is keyed on the distribution codename and lags
a new Ubuntu by months, and "Minimal" variants omit tooling the script expects.
The instance needs a **public IPv4 address** — the wizard will happily create one
without, and then nothing can reach it, SSH included.

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
it on the dense market scene. With only two OCPUs there is no second core to
borrow, either. The demo degrades to slow motion rather than
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

| Host | Card? | Free allowance | Verdict |
|---|---|---|---|
| **Cloudflare quick tunnel** | no | unlimited, no account | **Start here.** Instant public HTTPS from your own machine. Random URL, dies with the terminal. |
| **GitHub Codespaces** | no | 120 core-h + 15 GB/month | **Best no-card cloud option.** Stable URL, stops after 30 min idle. |
| **Azure for Students** | no | $100 / 12 months, renewable | The only no-card way to get an always-on VM. Needs a college email. |
| **Oracle Ampere A1** | yes | 2 OCPU / 12 GB, 10 TB, indefinite | Best fit on the merits. Halved in July 2026; sized for exactly one world. |
| **Hugging Face Spaces** | — | Docker Spaces need PRO | **No longer free.** Changed July 2026: Gradio and Docker Spaces require a paid plan; only static Spaces stay free, and this is not static. |
| **Google Cloud Run** | yes | 180k vCPU-s/month | Viable but needs a billing account. Bills CPU for the whole connection; 60-min cap cuts long sessions. |
| **Fly.io / Render / Railway** | yes | — | Cards required, and scale-to-zero suspends the simulation anyway. |
| **PythonAnywhere** | no | free tier | **Cannot work:** the free tier is WSGI-only, with no WebSocket support. |
| **AWS Lambda** | yes | — | See above. Wrong shape at any price. |

For demo day: the tunnel as the thing you actually present from, and a Codespace
kept warm as the backup with a stable link. Two routes, no shared failure mode,
neither needing a card.

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
opening any port, which side-steps the Oracle firewall layers entirely.
`scripts/share.sh` does this for you, or by hand on the server:

```bash
cloudflared tunnel --url http://localhost:8420
```

**Changing the code afterwards.** `/opt/sarathi` is a checkout that
`setup-oracle.sh` resets with `git reset --hard`, so it is not a place to edit:
keep your own clone in `$HOME` and move changes across.

```bash
sudo bash deploy/redeploy.sh                          # pull the branch, restart
sudo bash deploy/redeploy.sh --from ~/automated-driving  # uncommitted work
```

It syncs dependencies, restarts the service and waits on `/healthz` — a unit
that is `active` is not yet a demo that is serving. If it does not come back it
prints the journal and the command to roll back to the previous commit.

**Docker instead of systemd:**

```bash
docker build -t sarathi -f deploy/Dockerfile .
docker run --rm -p 8420:8420 sarathi
DOMAIN=demo.example.org docker compose -f deploy/compose.yaml up -d
```

Built and exercised on x86: 510 MB, starts unprivileged as uid 10001, reports
healthy on `/healthz`, and holds 1.00x real time with two viewers sharing the
world — including through Caddy, where a 20-second stream sustained 10.1
frames/s and the socket stayed open. The image is multi-arch by construction, so
the same file builds on the Ampere VM, but the ARM build itself has not been
run here.

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
  It also means the first visitor gets a world that has not been running. Pass
  `--keep-warm` to invert that trade, which is right wherever CPU is free and
  idleness is punished — an Always Free VM being the case in point.
