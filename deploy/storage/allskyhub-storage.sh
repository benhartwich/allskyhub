#!/bin/bash
# Grow the images' file system from the hub's admin page (server/src/allskyhub_server/
# admin/storage.py), run as root by allskyhub-storage.service when allskyhub-storage.path
# sees a request (Benjamin, 2026-11-05: netcup local block storage, grown at netcup).
#
# The hub (unprivileged) leaves /var/lib/allskyhub-server/storage/request. Nothing is
# taken from it: the disk is always the one mounted at /srv/allskyhub-img, it must be an
# ext4 file system on a whole virtual disk, and resize2fs only ever grows it to the
# disk's size. Like the deploy helper, the spool is read and written as the hub, never
# by root. Answers: state (running, ok, failed) and log.
set -euo pipefail

SPOOL=${ALLSKYHUB_STORAGE_SPOOL:-/var/lib/allskyhub-server/storage}
DISK=${ALLSKYHUB_STORAGE_MOUNT:-/srv/allskyhub-img}
HUB_USER=${ALLSKYHUB_HUB_USER:-allskyhub-server}
LOCK=${ALLSKYHUB_STORAGE_LOCK:-/run/allskyhub-storage.lock}
# Overridable only for trying the script out in a sandbox (a loop device).
DEV_RE=${ALLSKYHUB_STORAGE_DEVICE_RE:-^/dev/vd[a-z]+\$}
DEV_TYPE=${ALLSKYHUB_STORAGE_DEVICE_TYPE:-disk}

[[ -f $SPOOL/request ]] || exit 0
exec 9>"$LOCK"
flock -n 9 || exit 0
as_hub() { runuser -u "$HUB_USER" -- "$@"; }

# Removed as the hub, like everything in its folder: never by root through a link.
as_hub rm -f -- "$SPOOL/request" || exit 0
answer() {  # answer <file> <text>
    as_hub rm -f -- "$SPOOL/$1"
    # shellcheck disable=SC2016  # $1 is the inner shell's
    printf '%s\n' "$2" | as_hub sh -c 'umask 027; cat > "$1"' _ "$SPOOL/$1"
}
as_hub rm -f -- "$SPOOL/log"
# shellcheck disable=SC2016  # $1 is the inner shell's
as_hub sh -c 'umask 027; : > "$1"' _ "$SPOOL/log"
say() { printf '%s %s\n' "$(date '+%H:%M:%S')" "$*" | as_hub tee -a -- "$SPOOL/log" >/dev/null; }
run() { "$@" 2>&1 | as_hub tee -a -- "$SPOOL/log" >/dev/null; }
fail() { say "FEHLER: $*"; answer state failed; exit 0; }

answer state running
dev=$(findmnt -no SOURCE --target "$DISK" 2>/dev/null || true)
fstype=$(findmnt -no FSTYPE --target "$DISK" 2>/dev/null || true)
mountpoint -q "$DISK" || fail "$DISK ist nicht eingehängt."
[[ $dev =~ $DEV_RE ]] || fail "Unerwartetes Gerät unter $DISK: ${dev:-keins}."
[[ $fstype == ext4 ]] || fail "Unerwartetes Dateisystem: ${fstype:-keins}."
[[ $(lsblk -no TYPE "$dev") == "$DEV_TYPE" ]] || fail "$dev ist keine ganze Platte."

before=$(df -B1 --output=size "$DISK" | tail -1 | tr -d ' ')
say "Platte $dev: $(( $(lsblk -bndo SIZE "$dev") / 1000000000 )) GB, Dateisystem $(( before / 1000000000 )) GB."
say "resize2fs $dev"
if ! run resize2fs "$dev"; then
    fail "resize2fs hat nicht geklappt; das Dateisystem ist unverändert."
fi
after=$(df -B1 --output=size "$DISK" | tail -1 | tr -d ' ')
say "Fertig: Dateisystem jetzt $(( after / 1000000000 )) GB."
answer state ok
