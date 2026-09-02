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
REPO="${SARATHI_REPO:-https://github.com/Paragraph1148/automated-driving}"
BRANCH="${SARATHI_BRANCH:-main}"
APP=/opt/sarathi

echo "==> packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq curl git ca-certificates debian-keyring debian-archive-keyring apt-transport-https

echo "==> service user"
id -u sarathi &>/dev/null || useradd --system --home "$APP" --shell /usr/sbin/nologin sarathi

echo "==> code at $APP ($BRANCH)"
if [ -d "$APP/.git" ]; then
  git -C "$APP" fetch --depth 1 origin "$BRANCH"
  git -C "$APP" reset --hard "origin/$BRANCH"
else
  git clone --depth 1 --branch "$BRANCH" "$REPO" "$APP"
fi

echo "==> uv and dependencies"
# uv installs the interpreter too, so the VM's system Python is never touched
# and the ARM image's Python version does not have to be the one we need.
if [ ! -x /usr/local/bin/uv ]; then
  curl -LsSf https://astral.sh/uv/install.sh | UV_INSTALL_DIR=/usr/local/bin sh
fi
cd "$APP"
/usr/local/bin/uv sync --locked --no-dev
chown -R sarathi:sarathi "$APP"

echo "==> checking this machine can hold 20 Hz"
# An Ampere core is slower than a laptop's. Better to find that out now than
# in front of a judge; the demo still runs either way, just slower.
sudo -u sarathi "$APP/.venv/bin/python" scripts/hostcheck.py || true

echo "==> systemd unit"
install -m 644 "$APP/deploy/sarathi.service" /etc/systemd/system/sarathi.service
grep -q -- --keep-warm /etc/systemd/system/sarathi.service \
  || echo "note: unit is not using --keep-warm; see docs/05-hosting.md" >&2
systemctl daemon-reload
systemctl enable --now sarathi
systemctl restart sarathi

echo "==> caddy"
if ! command -v caddy &>/dev/null; then
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
    | tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  apt-get update -qq && apt-get install -y -qq caddy
fi
# {$DOMAIN} with no domain becomes :80 — plain HTTP, no certificate attempted.
cat >/etc/caddy/Caddyfile <<EOF
${DOMAIN:-:80} {
	encode zstd gzip
	reverse_proxy 127.0.0.1:8420 {
		flush_interval -1
	}
}
EOF
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
netfilter-persistent save >/dev/null 2>&1 || iptables-save >/etc/iptables/rules.v4

cat <<EOF

  done.

  local check :  curl -s localhost:8420/healthz
  through caddy: curl -sI localhost/
  public       : ${DOMAIN:+https://$DOMAIN}${DOMAIN:-http://$(curl -s --max-time 5 ifconfig.me || echo '<public ip>')}

  Still not reachable from outside? It is the VCN security list, not this box.
  Networking > Virtual Cloud Networks > your VCN > Security Lists > Default,
  add an ingress rule: source 0.0.0.0/0, TCP, destination port 80 and 443.

  logs: journalctl -u sarathi -f
EOF
