#!/usr/bin/env bash
# Why can nobody reach the demo?
#
#   sudo bash deploy/diagnose.sh [hostname]
#
# There are four things between a browser and the simulation, and all four fail
# the same way from outside — a timeout. This says which one it is.
#
#   1. the demo        listening on 127.0.0.1:8420
#   2. caddy           listening on 0.0.0.0:80 and :443
#   3. the host firewall   iptables on the instance itself
#   4. the VCN security list   in Oracle's console, invisible from in here
#
# Nothing here changes anything; it only looks.
set -uo pipefail

DOMAIN="${1:-}"
[ -z "$DOMAIN" ] && DOMAIN="$(grep -hoE '^[a-z0-9.-]+\.[a-z]{2,}' /etc/caddy/sites.d/*.caddy 2>/dev/null | head -1)"

ok()   { printf '  \033[32mok\033[0m    %s\n' "$1"; }
bad()  { printf '  \033[31mBAD\033[0m   %s\n' "$1"; }
note() { printf '        %s\n' "$1"; }

echo
echo "1. the demo"
if curl -fsS --max-time 3 http://127.0.0.1:8420/healthz >/dev/null 2>&1; then
  ok "serving on 127.0.0.1:8420"
else
  bad "not answering on 127.0.0.1:8420"
  note "sudo systemctl status sarathi ; journalctl -u sarathi -n 30"
fi

echo
echo "2. caddy"
if systemctl is-active --quiet caddy 2>/dev/null; then
  ok "running"
else
  bad "not running"
  note "sudo systemctl status caddy"
fi
LISTEN="$(ss -tlnH 2>/dev/null | awk '{print $4}')"
for port in 80 443; do
  if grep -qE "(^|[:.])${port}\$" <<<"$LISTEN"; then
    ok "listening on :$port"
  else
    bad "nothing listening on :$port"
  fi
done

echo
echo "3. the host firewall"
if iptables -C INPUT -p tcp --dport 80 -j ACCEPT 2>/dev/null \
   && iptables -C INPUT -p tcp --dport 443 -j ACCEPT 2>/dev/null; then
  ok "iptables accepts 80 and 443"
else
  bad "iptables has no ACCEPT for 80 and/or 443"
  note "sudo bash deploy/setup-oracle.sh adds these; or by hand:"
  note "  sudo iptables -I INPUT 5 -p tcp --dport 80 -j ACCEPT"
  note "  sudo iptables -I INPUT 5 -p tcp --dport 443 -j ACCEPT"
fi
REJECTS="$(iptables -S INPUT 2>/dev/null | grep -cE 'REJECT|DROP')"
[ "${REJECTS:-0}" -gt 0 ] && note "INPUT has $REJECTS REJECT/DROP rule(s) — order matters, ACCEPT must come first"

echo
echo "4. dns"
MYIP="$(curl -fsS --max-time 5 ifconfig.me 2>/dev/null || echo '')"
[ -n "$MYIP" ] && note "this instance's public IP: $MYIP"
if [ -n "$DOMAIN" ]; then
  RESOLVED="$(getent hosts "$DOMAIN" 2>/dev/null | awk '{print $1}' | head -1)"
  if [ -z "$RESOLVED" ]; then
    bad "$DOMAIN does not resolve yet"
    note "DNS may still be propagating, or the A record is missing"
  elif [ "$RESOLVED" = "$MYIP" ]; then
    ok "$DOMAIN -> $RESOLVED (this machine)"
  else
    bad "$DOMAIN -> $RESOLVED, but this machine is $MYIP"
    note "fix the A record; a certificate can never be issued while these differ"
  fi
else
  note "no hostname given or found in /etc/caddy/sites.d/"
fi

echo
echo "5. recent certificate attempts"
# grep -c already prints 0 and exits 1 when it finds nothing; an `|| echo 0`
# on top of that yields "0\n0", which is not a number.
ACME="$(journalctl -u caddy -n 400 --no-pager 2>/dev/null | grep -c 'challenge failed')"
ACME="${ACME:-0}"
if [ "${ACME:-0}" -gt 0 ]; then
  bad "$ACME failed challenges in the recent log"
  journalctl -u caddy -n 400 --no-pager 2>/dev/null \
    | grep -o '"detail":"[^"]*"' | tail -2 | sed 's/^/        /'
  note "Let's Encrypt allows 5 failures per hostname per hour, refilling one"
  note "every 12 minutes. Fix the cause before retrying, do not loop."
else
  ok "no failed challenges in the recent log"
fi

echo
echo "verdict"
if curl -fsS --max-time 3 http://127.0.0.1:8420/healthz >/dev/null 2>&1 \
   && systemctl is-active --quiet caddy 2>/dev/null \
   && iptables -C INPUT -p tcp --dport 80 -j ACCEPT 2>/dev/null; then
  cat <<EOF
  Everything on this box is correct, so what is left is the one layer this
  script cannot see: Oracle's VCN security list. It sits above the host
  firewall, blocks by default, and reports nothing to either side.

    Networking > Virtual Cloud Networks > <your VCN> > Security Lists > Default
    Add ingress rules:  source 0.0.0.0/0, IP protocol TCP,
                        destination port 80, then again for 443

  Then:  sudo systemctl restart caddy   and watch  journalctl -u caddy -f
EOF
else
  echo "  Fix the BAD lines above first, then run this again."
fi
echo
