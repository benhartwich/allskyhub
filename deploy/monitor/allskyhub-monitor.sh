#!/bin/bash
# Hub health checks every few minutes (roadmap #12): the site through nginx and TLS, free disk,
# certificate expiry, age of the database dump, and the server's incoming traffic per day
# (SharedSky stations send images; Benjamin, 2026-11-05). A message goes out only when a check changes
# between ok and failing (state in $STATE_DIR), to ntfy if ALLSKYHUB_MONITOR_NTFY_URL is set,
# otherwise only to the journal.
#
# Config (optional): /etc/allskyhub-server/monitor.env
#   ALLSKYHUB_MONITOR_NTFY_URL=https://ntfy.example.org/my-topic
#   ALLSKYHUB_MONITOR_NTFY_TOKEN=tk_...          (if the topic needs one)
#   ALLSKYHUB_MONITOR_MIN_FREE_PCT=15            (warn above 85 % used)
#   ALLSKYHUB_MONITOR_MAX_RX_GB_DAY=100          (incoming traffic of the server per UTC day)
#   ALLSKYHUB_MONITOR_IMAGES_MAX_PCT=70          (the images' own disk, warn early to grow it)
set -uo pipefail

SITE="${ALLSKYHUB_MONITOR_URL:-https://allskyhub.org}"
DATA_DIR="${ALLSKYHUB_SERVER_DATA_DIR:-/var/lib/allskyhub-server}"
STATE_DIR="${ALLSKYHUB_MONITOR_STATE_DIR:-$DATA_DIR/monitor}"
MIN_FREE_PCT="${ALLSKYHUB_MONITOR_MIN_FREE_PCT:-15}"
MAX_RX_GB="${ALLSKYHUB_MONITOR_MAX_RX_GB_DAY:-100}"
IMAGES_MAX_PCT="${ALLSKYHUB_MONITOR_IMAGES_MAX_PCT:-70}"
CERT_DAYS="${ALLSKYHUB_MONITOR_CERT_DAYS:-14}"
DUMP_MAX_H="${ALLSKYHUB_MONITOR_DUMP_MAX_HOURS:-26}"

mkdir -p "$STATE_DIR"

notify() {  # title, message, priority
  echo "allskyhub-monitor: $1: $2"
  [ -n "${ALLSKYHUB_MONITOR_NTFY_URL:-}" ] || return 0
  # The token through stdin (curl -H @-), never on the command line (/proc/*/cmdline).
  local auth=""
  [ -n "${ALLSKYHUB_MONITOR_NTFY_TOKEN:-}" ] && auth="Authorization: Bearer $ALLSKYHUB_MONITOR_NTFY_TOKEN"
  printf '%s\n' "$auth" | curl -fsS -m 15 -H @- -H "Title: $1" -H "Priority: $3" \
    -H "Tags: telescope" -d "$2" "$ALLSKYHUB_MONITOR_NTFY_URL" >/dev/null \
    || echo "allskyhub-monitor: ntfy failed" >&2
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

# 5. Incoming traffic of the server today (UTC), from the default route's interface. The
# counter is read every run; today's sum is kept in $STATE_DIR/rx-day (day, bytes, last).
iface="$(ip route show default 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "dev") print $(i + 1); exit}')"
if [ -n "$iface" ] && [ -r "/sys/class/net/$iface/statistics/rx_bytes" ]; then
  now_rx="$(cat "/sys/class/net/$iface/statistics/rx_bytes")"
  today="$(date -u +%F)"
  day="$today"; sum=0; last="$now_rx"
  [ -r "$STATE_DIR/rx-day" ] && read -r day sum last < "$STATE_DIR/rx-day"
  # A damaged state file starts anew instead of breaking the arithmetic.
  if ! [[ "$sum" =~ ^[0-9]+$ && "$last" =~ ^[0-9]+$ ]]; then day="$today"; sum=0; last="$now_rx"; fi
  delta=$(( now_rx - last ))
  [ "$delta" -lt 0 ] && delta="$now_rx"  # the counter started anew (reboot): all since boot
  if [ "$day" = "$today" ]; then sum=$(( sum + delta )); else day="$today"; sum="$delta"; fi
  echo "$day $sum $now_rx" > "$STATE_DIR/rx-day"
  gb=$(( sum / 1000000000 ))
  if [ "$gb" -lt "$MAX_RX_GB" ]; then
    report traffic ok "Eingehender Traffic heute wieder unter $MAX_RX_GB GB ($gb GB)."
  else
    report traffic fail "Heute schon $gb GB eingehender Traffic am Server ($iface), mehr als $MAX_RX_GB GB. Stationen (SharedSky) prüfen."
  fi
fi

# 6. The images on their own disk (netcup local block storage, since 2026-11-05: fstab binds
# /srv/allskyhub-img/images there). It must be mounted (else the hub does not start, by
# RequiresMountsFor) and warns early, from 70 % used, so there is time to grow it.
images="$DATA_DIR/images"
if grep -qs "[[:space:]]${images}[[:space:]]" /etc/fstab; then
  if ! mountpoint -q "$images"; then
    report images fail "Der Bildspeicher ist nicht eingehängt ($images): Block Storage bei netcup und /etc/fstab prüfen, dann mount -a."
  else
    read -r used size <<< "$(df -B1 --output=used,size "$images" | tail -1)"
    pct=$(( used * 100 / size ))
    left=$(( (size - used) / 1000000000 ))
    if [ "$pct" -lt "$IMAGES_MAX_PCT" ]; then
      report images ok "Bildspeicher wieder bei ${pct} % (${left} GB frei)."
    else
      report images fail "Bildspeicher zu ${pct} % voll, noch ${left} GB frei. Local Block Storage bei netcup vergrößern, danach: resize2fs $(findmnt -no SOURCE "$images" | sed 's/\[.*//')"
    fi
  fi
fi
