#!/bin/bash
# Daily dump of the hub database to a stable path for the server's external rsync
# (docs/betrieb.md, "Sicherung"). Runs as allskyhub-server with the hub's env file.
#
# Writes $ALLSKYHUB_SERVER_DATA_DIR/backup/allskyhub-YYYY-MM-DD.sql.gz (keeps 7) and
# allskyhub-latest.sql.gz (the newest). Restore: gunzip -c FILE | psql "$DATABASE_URL"
set -euo pipefail

DIR="${ALLSKYHUB_SERVER_DATA_DIR:-/var/lib/allskyhub-server}/backup"
KEEP="${ALLSKYHUB_BACKUP_KEEP:-7}"
OUT="$DIR/allskyhub-$(date +%F).sql.gz"

mkdir -p "$DIR"
tmp="$(mktemp "$DIR/.dump-XXXXXX")"
trap 'rm -f "$tmp"' EXIT
pg_dump --no-owner --no-privileges "$ALLSKYHUB_SERVER_DATABASE_URL" | gzip -9 > "$tmp"
# A dump without the schema's last table is broken: refuse to replace a good one.
gunzip -c "$tmp" | grep -q "CREATE TABLE public.user_account" || {
  echo "allskyhub-db-backup: dump looks incomplete, keeping the previous one" >&2
  exit 1
}
chmod 0640 "$tmp"
mv "$tmp" "$OUT"
ln -sfn "$(basename "$OUT")" "$DIR/allskyhub-latest.sql.gz"
ls -1t "$DIR"/allskyhub-????-??-??.sql.gz | tail -n +"$((KEEP + 1))" | xargs -r rm --
echo "allskyhub-db-backup: $(du -h "$OUT" | cut -f1) written to $OUT"
