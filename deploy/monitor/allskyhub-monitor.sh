#!/bin/bash
# Hub health checks every few minutes (roadmap #12): the site through nginx and TLS, free disk,
# certificate expiry, age of the database dump. A message goes out only when a check changes
# between ok and failing (state in $STATE_DIR), to ntfy if ALLSKYHUB_MONITOR_NTFY_URL is set,
# otherwise only to the journal.
#
# Config (optional): /etc/allskyhub-server/monitor.env
#   ALLSKYHUB_MONITOR_NTFY_URL=https://ntfy.example.org/my-topic
#   ALLSKYHUB_MONITOR_NTFY_TOKEN=tk_...          (if the topic needs one)
set -uo pipefail

SITE="${ALLSKYHUB_MONITOR_URL:-https://allskyhub.org}"
DATA_DIR="${ALLSKYHUB_SERVER_DATA_DIR:-/var/lib/allskyhub-server}"
STATE_DIR="${ALLSKYHUB_MONITOR_STATE_DIR:-$DATA_DIR/monitor}"
MIN_FREE_PCT="${ALLSKYHUB_MONITOR_MIN_FREE_PCT:-10}"
CERT_DAYS="${ALLSKYHUB_MONITOR_CERT_DAYS:-14}"
DUMP_MAX_H="${ALLSKYHUB_MONITOR_DUMP_MAX_HOURS:-26}"

mkdir -p "$STATE_DIR"

notify() {  # title, message, priority
  echo "allskyhub-monitor: $1: $2"
  [ -n "${ALLSKYHUB_MONITOR_NTFY_URL:-}" ] || return 0
  auth=()
  [ -n "${ALLSKYHUB_MONITOR_NTFY_TOKEN:-}" ] && auth=(-H "Authorization: Bearer $ALLSKYHUB_MONITOR_NTFY_TOKEN")
  curl -fsS -m 15 "${auth[@]}" -H "Title: $1" -H "Priority: $3" -H "Tags: telescope" \
    -d "$2" "$ALLSKYHUB_MONITOR_NTFY_URL" >/dev/null || echo "allskyhub-monitor: ntfy failed" >&2
}

report() {  # name, ok|fail, message
  local file="$STATE_DIR/$1" before
  before="$(cat "$file" 2>/dev/null || echo ok)"
  if [ "$2" != "$before" ]; then
    if [ "$2" = fail ]; then
      notify "allskyhub: $1" "$3" high
    else
      notify "allskyhub: $1 wieder ok" "$3" default
    fi
    echo "$2" > "$file"
  fi
}

# 1. The site answers through nginx and TLS, and the database is reachable (/healthz).
if body="$(curl -fsS -m 15 "$SITE/healthz" 2>&1)" && [[ "$body" == *'"ok"'* ]]; then
  report site ok "$SITE antwortet wieder."
else
  report site fail "$SITE/healthz antwortet nicht: ${body:0:200}"
fi

# 2. Free disk where images and dumps live.
free_pct="$(df --output=pcent "$DATA_DIR" | tail -1 | tr -dc 0-9)"
free_pct=$((100 - free_pct))
if [ "$free_pct" -ge "$MIN_FREE_PCT" ]; then
  report disk ok "Wieder ${free_pct} % frei unter $DATA_DIR."
else
  report disk fail "Nur noch ${free_pct} % frei unter $DATA_DIR."
fi

# 3. TLS certificate as served (catches a failed renewal before visitors do).
host="${SITE#https://}"; host="${host%%/*}"
end="$(echo | timeout 15 openssl s_client -connect "$host:443" -servername "$host" 2>/dev/null \
  | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)"
if [ -n "$end" ] && days=$(( ($(date -d "$end" +%s) - $(date +%s)) / 86400 )) && [ "$days" -ge "$CERT_DAYS" ]; then
  report cert ok "Zertifikat gilt noch $days Tage."
else
  report cert fail "Zertifikat für $host läuft bald ab oder ist nicht lesbar (${end:-unbekannt}). Verlängerung prüfen."
fi

# 4. The nightly database dump (deploy/backup) is recent.
latest="$DATA_DIR/backup/allskyhub-latest.sql.gz"
if [ -e "$latest" ] && [ $(( $(date +%s) - $(stat -L -c %Y "$latest") )) -le $((DUMP_MAX_H * 3600)) ]; then
  report backup ok "Datenbank-Dump ist aktuell."
else
  report backup fail "Kein Datenbank-Dump in den letzten $DUMP_MAX_H Stunden ($latest)."
fi
