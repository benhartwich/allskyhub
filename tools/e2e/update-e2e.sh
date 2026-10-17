#!/usr/bin/env bash
# End-to-end test of agent updates (SPEC §8) on the built image, as a camera would do them:
# the image boots in a container (systemd-nspawn, its own network), a local channel offers
# a newer release signed with a test key, and the image's own updater installs it, rolls it
# back when asked, and rolls back a broken release by itself.
#
#   sudo tools/e2e/update-e2e.sh build/allskyhub-<v>.img.xz build/allskyhub-agent-<v>-arm64.tar.xz
#
# Needs an arm64 host with systemd-container (machinectl, systemd-nspawn) and openssl.
set -euo pipefail

IMG_XZ=$1
BUNDLE=$2
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
WORK=$(mktemp -d)
MNT=$WORK/root
CAM=allskyhub-e2e
LOOP=""

cleanup() {
    set +e
    machinectl terminate "$CAM" 2>/dev/null
    for _ in $(seq 30); do machinectl status "$CAM" >/dev/null 2>&1 || break; sleep 1; done
    mountpoint -q "$MNT" && umount "$MNT"
    [ -n "$LOOP" ] && losetup -d "$LOOP"
    rm -rf "$WORK"
}
trap cleanup EXIT

say() { echo "== $*"; }
fail() { echo "::error::$*"; journalctl -M "$CAM" --no-pager -n 200 2>/dev/null || true; exit 1; }
inside() { systemd-run -M "$CAM" --wait --pipe --quiet -- "$@"; }

say "image"
xz -dc "$IMG_XZ" > "$WORK/disk.img"
LOOP=$(losetup -fP --show "$WORK/disk.img")
mkdir -p "$MNT"
mount "${LOOP}p2" "$MNT"
OPT=$MNT/opt/allskyhub-agent
VER=$(basename "$(readlink "$OPT/current")")
NEXT="${VER%.*}.$(( ${VER##*.} + 100 ))"
BROKEN="${VER%.*}.$(( ${VER##*.} + 101 ))"
say "installed $VER; offering $NEXT, then the broken $BROKEN"

say "camera setup for the container"
# Nothing that touches hardware, partitions or the network; the updater only when asked.
for u in allskyhub-firstboot.service allskyhub-setupfile.service allskyhub-setup-key.service \
        allskyhub-wifi-country.path allskyhub-updater.timer allskyhub-watchdog.timer \
        nftables.service NetworkManager.service NetworkManager-wait-online.service \
        ModemManager.service wpa_supplicant.service bluetooth.service userconfig.service \
        cloud-init.target cloud-init-local.service cloud-init-main.service cloud-init-network.service \
        cloud-config.service cloud-final.service rpi-resize.service sshswitch.service \
        regenerate_ssh_host_keys.service; do
    ln -sf /dev/null "$MNT/etc/systemd/system/$u"
done
# The simulated camera: frames without hardware, so "healthy" includes capturing.
cat > "$MNT/etc/allskyhub-agent/allskyhub-agent.env" <<'ENV'
ALLSKYHUB_AGENT_ARGS="--sim --lat 50.04 --lon 10.39 run --data /var/lib/allskyhub-agent/data --network none --http 127.0.0.1:8080"
ENV
echo "ALLSKYHUB_UPDATE_MANIFEST_URL=http://127.0.0.1:8000/manifest.json" \
    > "$MNT/etc/allskyhub-agent/updater.env"
openssl genpkey -algorithm ed25519 -out "$WORK/key.pem" 2>/dev/null
openssl pkey -in "$WORK/key.pem" -pubout -out "$MNT/etc/allskyhub-agent/update-keys/e2e.pem"
install -d "$MNT/srv/e2e"

# A release as the CI publishes one, from the image's bundle with another version.
publish() {  # publish VERSION [broken]
    local v=$1 d site
    d=$(mktemp -d -p "$WORK")
    tar -C "$d" -xJf "$BUNDLE"
    mv "$d/$VER" "$d/$v"
    # Scripts name their interpreter by the release path; links stay links.
    find "$d/$v/.venv/bin" -type f -exec sed -i "s|/releases/$VER/|/releases/$v/|g" {} +
    site=$(echo "$d/$v"/.venv/lib/python3*/site-packages)
    sed -i "s/^__version__ = .*/__version__ = \"$v\"/" "$site/allskyhub_agent/__init__.py"
    if [ "${2:-}" = broken ]; then
        echo 'raise RuntimeError("e2e: a release that cannot start")' >> "$site/allskyhub_agent/__init__.py"
    fi
    local name="allskyhub-agent-$v-arm64.tar.xz"
    tar -C "$d" --owner=0 --group=0 --numeric-owner -cJf "$MNT/srv/e2e/$name" "$v"
    python3 "$ROOT/tools/release/make_manifest.py" "$MNT/srv/e2e/$name" "$v" \
        "http://127.0.0.1:8000/$name" > "$MNT/srv/e2e/manifest.json"
    openssl pkeyutl -sign -inkey "$WORK/key.pem" -rawin -in "$MNT/srv/e2e/manifest.json" \
        -out "$MNT/srv/e2e/manifest.json.sig"
    rm -rf "$d"
}

state() { python3 -c 'import json,sys; s=json.load(open(sys.argv[1])); print(s.get("state"), s.get("version"), s.get("code"))' \
    "$MNT/var/lib/allskyhub-updater/state.json" 2>/dev/null || echo "none"; }
current() { basename "$(readlink "$OPT/current")"; }

ask() {  # ask WORD: what the local UI does (SPEC §8 Asked for in the local UI)
    inside sh -c "echo $1 > /var/lib/allskyhub-agent/update-request"
}

wait_state() {  # wait_state "STATE VERSION" SECONDS
    local want=$1 until=$((SECONDS + $2))
    while [ $SECONDS -lt $until ]; do
        case "$(state)" in "$want"*) return 0 ;; esac
        sleep 5
    done
    fail "updater state is '$(state)', expected '$want'"
}

health() {
    inside /usr/bin/python3 -c 'import json,urllib.request; print(json.load(urllib.request.urlopen("http://127.0.0.1:8080/api/health", timeout=5)).get("version"))' 2>/dev/null || true
}

say "boot"
systemd-nspawn --quiet --boot --private-network --machine "$CAM" --directory "$MNT" \
    > "$WORK/console.log" 2>&1 &
for _ in $(seq 120); do
    inside true 2>/dev/null && break
    sleep 2
done
timeout 300 systemd-run -M "$CAM" --wait --pipe --quiet -- systemctl is-system-running --wait \
    >/dev/null 2>&1 || true
systemd-run -M "$CAM" --unit e2e-channel --quiet -- \
    /usr/bin/python3 -m http.server 8000 --bind 127.0.0.1 --directory /srv/e2e
for _ in $(seq 60); do [ "$(health)" = "$VER" ] && break; sleep 5; done
[ "$(health)" = "$VER" ] || fail "agent $VER did not come up"

say "1. a newer release is installed"
publish "$NEXT"
ask now
wait_state "installed $NEXT" 600
[ "$(current)" = "$NEXT" ] || fail "current is $(current), not $NEXT"
[ "$(health)" = "$NEXT" ] || fail "the running agent is not $NEXT"

say "2. rolled back when asked"
ask rollback
wait_state "rolled_back $NEXT" 600
[ "$(current)" = "$VER" ] || fail "current is $(current) after the rollback, not $VER"
[ "$(health)" = "$VER" ] || fail "the running agent is not $VER after the rollback"

say "3. a release that cannot start is rolled back by itself"
publish "$BROKEN" broken
ask now
wait_state "rolled_back $BROKEN" 900
[ "$(current)" = "$VER" ] || fail "current is $(current) after the broken release, not $VER"
[ "$(health)" = "$VER" ] || fail "the running agent is not $VER after the broken release"

say "passed"
