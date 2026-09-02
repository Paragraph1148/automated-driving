#!/usr/bin/env bash
# Put the live demo on the public internet from whatever machine you are sitting
# at, with no account, no credit card and no server.
#
#   ./scripts/share.sh
#   ./scripts/share.sh --port 9000 --scenario market_dense_mixed
#
# It starts the demo on loopback, opens a Cloudflare quick tunnel to it, and
# prints an https://<random>.trycloudflare.com URL that anyone can open. Ctrl-C
# stops both.
#
# Why this works at all: the page and the telemetry socket share one port, and
# the viewer derives wss:// from the page's own origin, so a single tunnel
# carries both and TLS terminates at Cloudflare. Two ports would need two
# tunnels and the socket would still be plaintext.
#
# The catch, stated plainly: the URL is random and changes every run, and the
# demo lives only as long as this machine stays awake with the terminal open.
# For a judged demo that is usually the right trade — the laptop is in the room
# anyway. For a link that outlives the session, see docs/05-hosting.md.
set -euo pipefail

PORT=8420
SCENARIO=village_road_unmarked
while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --scenario) SCENARIO="$2"; shift 2 ;;
    -h|--help) sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/sarathi"
LOG_DIR="$(mktemp -d)"
mkdir -p "$CACHE"

# --- cloudflared, without needing root or a package manager -----------------
CFD="$(command -v cloudflared || true)"
if [ -z "$CFD" ]; then
  CFD="$CACHE/cloudflared"
  if [ ! -x "$CFD" ]; then
    case "$(uname -s)-$(uname -m)" in
      Linux-x86_64)          ASSET=cloudflared-linux-amd64 ;;
      Linux-aarch64|Linux-arm64) ASSET=cloudflared-linux-arm64 ;;
      Darwin-arm64)          ASSET=cloudflared-darwin-arm64.tgz ;;
      Darwin-x86_64)         ASSET=cloudflared-darwin-amd64.tgz ;;
      *) echo "no cloudflared build for $(uname -s)-$(uname -m); install it yourself" >&2; exit 1 ;;
    esac
    echo "==> fetching cloudflared ($ASSET)"
    URL="https://github.com/cloudflare/cloudflared/releases/latest/download/$ASSET"
    if [ "${ASSET##*.}" = "tgz" ]; then
      curl -fsSL "$URL" | tar -xz -C "$CACHE" cloudflared
    else
      curl -fsSL -o "$CFD" "$URL"
    fi
    chmod +x "$CFD"
  fi
fi

cleanup() {
  # Kill the tunnel first so nobody is routed at a server that is going away.
  [ -n "${TUNNEL_PID:-}" ] && kill "$TUNNEL_PID" 2>/dev/null || true
  [ -n "${SERVE_PID:-}" ]  && kill "$SERVE_PID"  2>/dev/null || true
  rm -rf "$LOG_DIR"
}
trap cleanup EXIT INT TERM

# --- the demo, on loopback: the tunnel is the only way in -------------------
echo "==> starting the demo on 127.0.0.1:$PORT"
( cd "$ROOT" && uv run sarathi serve --port "$PORT" --scenario "$SCENARIO" \
    >"$LOG_DIR/serve.log" 2>&1 ) &
SERVE_PID=$!

for _ in $(seq 60); do
  if curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then break; fi
  if ! kill -0 "$SERVE_PID" 2>/dev/null; then
    echo "the demo failed to start:" >&2; cat "$LOG_DIR/serve.log" >&2; exit 1
  fi
  sleep 0.5
done

# --- the tunnel -------------------------------------------------------------
echo "==> opening a Cloudflare quick tunnel"
"$CFD" tunnel --url "http://127.0.0.1:$PORT" --no-autoupdate \
    >"$LOG_DIR/tunnel.log" 2>&1 &
TUNNEL_PID=$!

PUBLIC=""
for _ in $(seq 60); do
  PUBLIC="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG_DIR/tunnel.log" | head -1 || true)"
  [ -n "$PUBLIC" ] && break
  if ! kill -0 "$TUNNEL_PID" 2>/dev/null; then
    echo "the tunnel failed to start:" >&2; cat "$LOG_DIR/tunnel.log" >&2; exit 1
  fi
  sleep 1
done

if [ -z "$PUBLIC" ]; then
  echo "the tunnel never printed a URL. Its log:" >&2
  tail -20 "$LOG_DIR/tunnel.log" >&2
  exit 1
fi

# A URL is not the same as a working tunnel — cloudflared prints one before the
# edge connection is up, and a blocked network fails only at this point.
echo "==> checking the tunnel end to end"
REACHABLE=no
for _ in $(seq 30); do
  if curl -fsS --max-time 5 "$PUBLIC/healthz" >/dev/null 2>&1; then REACHABLE=yes; break; fi
  sleep 2
done

echo
echo "  ┌─────────────────────────────────────────────────────────────"
echo "  │  SARATHI live"
echo "  │"
echo "  │    $PUBLIC"
echo "  │"
if [ "$REACHABLE" = yes ]; then
  echo "  │  reachable — send that link to anyone"
else
  echo "  │  WARNING: the URL is up but this machine could not reach it."
  echo "  │  Usually a network that blocks the tunnel (some campus and"
  echo "  │  corporate networks do). Try another network, or a phone"
  echo "  │  hotspot, before demo day."
fi
echo "  │"
echo "  │  local: http://127.0.0.1:$PORT     scenario: $SCENARIO"
echo "  │  Ctrl-C stops the demo and the tunnel."
echo "  └─────────────────────────────────────────────────────────────"
echo

wait "$SERVE_PID"
