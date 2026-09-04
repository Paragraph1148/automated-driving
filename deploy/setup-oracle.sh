#!/usr/bin/env bash
# Provision the live demo on a fresh Oracle Cloud always-free VM (Ubuntu).
#
#   scp deploy/setup-oracle.sh ubuntu@<ip>:  &&  ssh ubuntu@<ip> 'sudo bash setup-oracle.sh'
#
# Idempotent: safe to re-run after an edit, it just re-pulls and restarts.
#
# Pass a domain to get automatic TLS, or leave it out to serve plain HTTP on
# port 80 for a first smoke test:
#
#   sudo bash setup-oracle.sh demo.example.org
set -euo pipefail

# Oracle's default image is Oracle Linux, which has dnf rather than apt. Say so
# in one line now instead of failing three commands in with a confusing error.
if ! command -v apt-get >/dev/null 2>&1; then
  echo "This script is written for the Ubuntu images (apt-get)." >&2
  echo "You appear to be on: $(. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME")" >&2
  echo "Recreate the instance choosing Canonical Ubuntu 22.04, or port the" >&2
  echo "package steps to dnf — the rest of the script is distro-agnostic." >&2
  exit 1
fi

DOMAIN="${1:-}"
# Let's Encrypt uses this to warn about expiry problems. Optional but worth
# setting on anything you intend to leave running: ACME_EMAIL=you@example.org
ACME_EMAIL="${ACME_EMAIL:-}"
REPO="${SARATHI_REPO:-https://github.com/Paragraph1148/automated-driving}"
APP=/opt/sarathi

# Default to the branch this script is being run from rather than main. The
# deployment assets can live only on a feature branch, and cloning main then
# produces a checkout with no deploy/ directory — which fails much later, at
# `install`, with an error that says nothing about branches. Falls back to main
# when the script was copied somewhere on its own.
_here="$(cd "$(dirname "$(readlink -f "$0")")" 2>/dev/null && pwd || true)"
_own_branch="$(git -C "${_here:-.}/.." rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
case "$_own_branch" in ""|HEAD) _own_branch=main ;; esac
BRANCH="${SARATHI_BRANCH:-$_own_branch}"

# uv downloads its own interpreter when the system has no matching Python. As
# root that lands in /root/.local/share/uv/python, and /root is mode 700 — so
# the venv symlinks to a binary the service user cannot execute, and the demo
# dies with "Permission denied" on a path that plainly exists. Put managed
# interpreters somewhere every user can read.
export UV_PYTHON_INSTALL_DIR=/opt/uv/python

echo "==> packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq curl git ca-certificates debian-keyring debian-archive-keyring apt-transport-https

echo "==> service user"
id -u sarathi &>/dev/null || useradd --system --home "$APP" --shell /usr/sbin/nologin sarathi

echo "==> code at $APP ($BRANCH)"
if [ -d "$APP/.git" ]; then
  git -C "$APP" fetch --depth 1 origin "$BRANCH"
  # FETCH_HEAD, not origin/$BRANCH: a shallow fetch of a branch the clone was
  # not made from does not necessarily update the remote-tracking ref.
  git -C "$APP" reset --hard FETCH_HEAD
else
  git clone --depth 1 --branch "$BRANCH" "$REPO" "$APP"
fi

# Fail here, naming the cause, rather than at `install` several steps later.
for needed in deploy/sarathi.service scripts/hostcheck.py; do
  [ -e "$APP/$needed" ] && continue
  cat >&2 <<EOF

  $BRANCH does not contain $needed.

  The deployment files are probably on another branch. Re-run pointing at it:

    sudo SARATHI_BRANCH=claude/sarathi-hosting-options-ey26qa bash $0 $DOMAIN

EOF
  exit 1
done

echo "==> uv and dependencies"
# uv installs the interpreter too, so the VM's system Python is never touched
# and the ARM image's Python version does not have to be the one we need.
if [ ! -x /usr/local/bin/uv ]; then
  curl -LsSf https://astral.sh/uv/install.sh | UV_INSTALL_DIR=/usr/local/bin sh
fi
mkdir -p "$UV_PYTHON_INSTALL_DIR"
chmod -R a+rX /opt/uv
# A venv from an earlier run can point at an interpreter under /root that the
# service user cannot execute. Rebuild it rather than inherit the problem.
if [ -d "$APP/.venv" ] && ! sudo -u sarathi test -x "$APP/.venv/bin/python"; then
  echo "    existing venv is unusable by the service user; rebuilding it"
  rm -rf "$APP/.venv"
fi
cd "$APP"
/usr/local/bin/uv sync --locked --no-dev
# The interpreter uv just fetched must stay readable by the service user too.
chmod -R a+rX /opt/uv
chown -R sarathi:sarathi "$APP"

echo "==> checking this machine can hold 20 Hz"
# An Ampere core is slower than a laptop's. Better to find that out now than
# in front of a judge; the demo still runs either way, just slower.
#
# On a re-run the service is already up, and with --keep-warm it is pinning the
# core. Measuring against that halves every number and makes a machine that
# holds real time look like one that cannot. Stop it first; the restart further
# down brings it back.
if systemctl is-active --quiet sarathi 2>/dev/null; then
  echo "    stopping the running demo so the measurement is not competing with it"
  systemctl stop sarathi
fi
sudo -u sarathi env HOME="$APP" "$APP/.venv/bin/python" scripts/hostcheck.py || true

echo "==> systemd unit"
install -m 644 "$APP/deploy/sarathi.service" /etc/systemd/system/sarathi.service
grep -q -- --keep-warm /etc/systemd/system/sarathi.service \
  || echo "note: unit is not using --keep-warm; see docs/05-hosting.md" >&2
systemctl daemon-reload
systemctl enable --now sarathi
systemctl restart sarathi

echo "==> caddy"
if ! command -v caddy &>/dev/null; then
  # Caddy's apt repository is keyed on the distribution codename, so a very
  # recently released Ubuntu can be missing from it for months. Do not let that
  # fail three steps later as an unexplained apt error.
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
    | tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  if ! { apt-get update -qq && apt-get install -y -qq caddy; }; then
    CODENAME="$(. /etc/os-release && echo "${VERSION_CODENAME:-unknown}")"
    rm -f /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -qq || true
    cat >&2 <<EOF

  Caddy has no package for this release ($CODENAME).

  The demo itself is installed and running on 127.0.0.1:8420 — only the TLS
  proxy in front of it is missing. Two ways forward:

    - Recreate the instance on Ubuntu 24.04 LTS, which the repository covers,
      and re-run this script. Cheapest if you have not configured anything yet.
    - Or skip Caddy and expose the demo through a Cloudflare tunnel instead:
        sudo snap install cloudflared   # or fetch the arm64 binary
        cloudflared tunnel --url http://localhost:8420
      That also removes the need for the VCN ingress rule below.

EOF
    exit 1
  fi
fi
# One file per site, so this box can host other projects and re-running this
# script cannot wipe them out. The main Caddyfile is written once and then left
# alone; sarathi owns only its own site file. See deploy/caddy/README.txt.
mkdir -p /etc/caddy/sites.d
if [ ! -f /etc/caddy/Caddyfile ] || ! grep -q 'sites\.d' /etc/caddy/Caddyfile; then
  [ -f /etc/caddy/Caddyfile ] && cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.bak
  cat >/etc/caddy/Caddyfile <<EOF
# Global options, then one file per site in sites.d.
# Do not add sites here — add /etc/caddy/sites.d/<project>.caddy instead.
{
${ACME_EMAIL:+	email $ACME_EMAIL}
}

import /etc/caddy/sites.d/*.caddy
EOF
fi

# With no domain this becomes :80, which answers on the IP for a smoke test.
# It also catches every unmatched hostname, so replace it with a real name
# before adding a second project.
cat >/etc/caddy/sites.d/sarathi.caddy <<EOF
${DOMAIN:-:80} {
	encode zstd gzip

	# The telemetry frames are ~13 KiB of JSON ten times a second and the
	# connection stays open as long as someone is watching, so the proxy must
	# not decide the stream has stalled and cut it.
	reverse_proxy 127.0.0.1:8420 {
		flush_interval -1
	}
}
EOF

# A bad config should not take the other projects down with it: validate first,
# and reload rather than restart so a failure leaves the running config intact.
if ! caddy validate --config /etc/caddy/Caddyfile 2>/dev/null; then
  echo "caddy config did not validate; leaving the running config alone" >&2
  caddy validate --config /etc/caddy/Caddyfile >&2 || true
  exit 1
fi
systemctl enable --now caddy
systemctl reload caddy || systemctl restart caddy

echo "==> firewall"
# The one that catches everyone: Oracle's Ubuntu images ship an iptables REJECT
# for everything but SSH, *underneath* the VCN security list. Opening 80/443 in
# the web console alone leaves the port dead, with no error to read anywhere.
for p in 80 443; do
  iptables -C INPUT -p tcp --dport "$p" -j ACCEPT 2>/dev/null \
    || iptables -I INPUT 5 -p tcp --dport "$p" -j ACCEPT
done
apt-get install -y -qq iptables-persistent >/dev/null 2>&1 || true
# The fallback writes into /etc/iptables, which a Minimal image may not have.
mkdir -p /etc/iptables
netfilter-persistent save >/dev/null 2>&1 || iptables-save >/etc/iptables/rules.v4

# ${DOMAIN:-x} returns DOMAIN when it is set, so the obvious one-liner printed
# the name twice. Decide it plainly instead.
if [ -n "$DOMAIN" ]; then
  PUBLIC_URL="https://$DOMAIN"
else
  PUBLIC_URL="http://$(curl -s --max-time 5 ifconfig.me || echo '<public ip>')"
fi

cat <<EOF

  done.

  local check :  curl -s localhost:8420/healthz
  through caddy: curl -sI localhost/
  public       : $PUBLIC_URL

  Still not reachable from outside? It is the VCN security list, not this box.
  Networking > Virtual Cloud Networks > your VCN > Security Lists > Default,
  add an ingress rule: source 0.0.0.0/0, TCP, destination port 80 and 443.

  logs: journalctl -u sarathi -f
EOF
