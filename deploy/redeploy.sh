#!/usr/bin/env bash
# Push a change into the running demo, without the full setup script.
#
#   sudo bash deploy/redeploy.sh                  # pull the branch and restart
#   sudo bash deploy/redeploy.sh --ref my-branch  # a different ref
#   sudo bash deploy/redeploy.sh --from ~/automated-driving   # uncommitted work
#
# setup-oracle.sh is for building the box. This is for the loop you are in
# afterwards: it touches only the code, the dependencies and the service.
#
# Why it exists: /opt/sarathi is a git checkout that setup-oracle.sh resets with
# `git reset --hard`. Editing there and re-running setup would silently destroy
# the work. Keep your own clone in $HOME, and move changes across with this.
set -euo pipefail

APP=/opt/sarathi
REF=""
FROM=""

while [ $# -gt 0 ]; do
  case "$1" in
    --ref) REF="$2"; shift 2 ;;
    --from) FROM="$2"; shift 2 ;;
    -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }
[ -d "$APP/.git" ] || { echo "no deployment at $APP — run setup-oracle.sh first" >&2; exit 1; }

WAS="$(git -C "$APP" rev-parse --short HEAD)"

if [ -n "$FROM" ]; then
  # Working-tree deploy: what you have in front of you, committed or not. Good
  # for iterating, bad for anything you want to be able to reproduce later.
  [ -d "$FROM" ] || { echo "no directory at $FROM" >&2; exit 1; }
  echo "==> copying $FROM -> $APP (excluding .git and .venv)"
  rsync -a --delete \
    --exclude='.git/' --exclude='.venv/' --exclude='__pycache__/' \
    --exclude='artifacts/' --exclude='.pytest_cache/' \
    "${FROM%/}/" "$APP/"
  echo "    deployed from a working tree; not a reproducible commit"
else
  REF="${REF:-$(git -C "$APP" rev-parse --abbrev-ref HEAD)}"
  echo "==> fetching $REF"
  git -C "$APP" fetch --depth 1 origin "$REF"
  git -C "$APP" reset --hard FETCH_HEAD
fi

chown -R sarathi:sarathi "$APP"

echo "==> syncing dependencies"
# HOME is $APP: the service user was created with --home /opt/sarathi, and
# /home/sarathi does not exist. UV_PYTHON_INSTALL_DIR must match what
# setup-oracle.sh used, or uv re-downloads the interpreter into a directory
# this user cannot read back.
sudo -u sarathi env HOME="$APP" UV_PYTHON_INSTALL_DIR=/opt/uv/python \
  /usr/local/bin/uv sync --locked --no-dev --project "$APP"

echo "==> restarting"
systemctl restart sarathi

# A restart that comes back unhealthy is the failure worth catching: the service
# is "active" long before it is serving.
PORT="$(grep -oE -- '--port [0-9]+' /etc/systemd/system/sarathi.service | awk '{print $2}')"
PORT="${PORT:-8420}"
for _ in $(seq 40); do
  if curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then
    NOW="$(git -C "$APP" rev-parse --short HEAD 2>/dev/null || echo working-tree)"
    echo
    echo "  healthy on :$PORT   $WAS -> $NOW"
    exit 0
  fi
  sleep 0.5
done

echo >&2
echo "  did not come back healthy. Last log lines:" >&2
journalctl -u sarathi -n 25 --no-pager >&2
echo >&2
echo "  to go back:  sudo git -C $APP reset --hard $WAS && sudo systemctl restart sarathi" >&2
exit 1
