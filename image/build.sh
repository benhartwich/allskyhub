#!/usr/bin/env bash
# Builds the allskyhub image from Raspberry Pi OS Lite (arm64, Trixie) for the Raspberry Pi 4
# and 5 (SPEC §8, milestone M5). Runs as root on an arm64 host (GitHub runner ubuntu-24.04-arm):
# native chroot, no emulation. Modelled on myboxi's image/build.sh.
#
#   sudo image/build.sh
#
# Output: build/allskyhub-<version>.img.xz and .sha256, and the agent update bundle
#         build/allskyhub-agent-<agent version>-arm64.tar.xz (docs/updates.md)
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
OUT=${OUT:-$ROOT/build}
VERSION=${ALLSKYHUB_IMAGE_VERSION:-$(date -u +%Y.%m.%d)-$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo dev)}
EXTRA_MB=${EXTRA_MB:-1536}
AGENT_VERSION=$(sed -n 's/^version = "\(.*\)"$/\1/p' "$ROOT/agent/pyproject.toml" | head -n 1)

# Pinned base image (update URL and checksum together).
BASE_URL=https://downloads.raspberrypi.com/raspios_lite_arm64/images/raspios_lite_arm64-2026-09-15/2026-09-15-raspios-trixie-arm64-lite.img.xz
BASE_SHA256=cdf4f3bfac35ae947b46e4e767f935453810549779ac3290e05a6754aee627e5

# ZWO ASI SDK 1.39 (MIT, (c) ZWO), pinned to the copy in the Allsky repository.
ASI_REF=14c268f891847a8f7222ce3ceb8ca8441659a450
ASI_BASE=https://raw.githubusercontent.com/AllskyTeam/allsky/$ASI_REF
ASI_LIB_SHA256=2ddd5ac4b1912867b890e65999f86abb9abc46ce91664ba5d47e4ceb82fd7b21
ASI_LICENSE_SHA256=995ebdaf782d676b750aa291ae649bc7df9e5c036b83cc5053779c9b5b7869c4
ASI_RULES_SHA256=c687c9026482441f65f9e2b5ed87382f59cb12b85497d78f90e137dfab83224a

UV_BIN=${UV_BIN:-$(command -v uv)}
IMG="$OUT/allskyhub-$VERSION.img"
MNT="$OUT/mnt"
LOOP=""

cleanup() {
    set +e
    for m in dev/pts dev sys proc boot/firmware ""; do
        mountpoint -q "$MNT/$m" && umount -l "$MNT/$m"
    done
    [ -n "$LOOP" ] && losetup -d "$LOOP"
}
trap cleanup EXIT

[ "$(uname -m)" = "aarch64" ] || { echo "needs an arm64 host"; exit 1; }
[ "$(id -u)" = 0 ] || { echo "needs root"; exit 1; }
mkdir -p "$OUT/cache" "$MNT"

fetch() {  # fetch URL SHA256 DEST
    [ -f "$3" ] || curl -fL --retry 3 -o "$3" "$1"
    echo "$2  $3" | sha256sum -c -
}

echo "== base image and ZWO SDK"
BASE="$OUT/cache/$(basename "$BASE_URL")"
fetch "$BASE_URL" "$BASE_SHA256" "$BASE"
fetch "$ASI_BASE/server/assets/asi_sdk/lib/armv8/libASICamera2.so.1.39" "$ASI_LIB_SHA256" \
    "$OUT/cache/libASICamera2.so.1.39"
fetch "$ASI_BASE/server/assets/asi_sdk/license.txt" "$ASI_LICENSE_SHA256" "$OUT/cache/asi-license.txt"
fetch "$ASI_BASE/config_repo/asi.rules" "$ASI_RULES_SHA256" "$OUT/cache/asi.rules"
xz -dc "$BASE" > "$IMG"

echo "== grow root partition by ${EXTRA_MB} MB"
truncate -s "+${EXTRA_MB}M" "$IMG"
parted -s "$IMG" resizepart 2 100%
LOOP=$(losetup -fP --show "$IMG")
e2fsck -pf "${LOOP}p2" || [ $? -le 1 ]
resize2fs "${LOOP}p2"

echo "== mount"
mount "${LOOP}p2" "$MNT"
mount "${LOOP}p1" "$MNT/boot/firmware"
for m in proc sys dev dev/pts; do mount --bind "/$m" "$MNT/$m"; done
if [ -e "$MNT/etc/resolv.conf" ] || [ -L "$MNT/etc/resolv.conf" ]; then
    mv "$MNT/etc/resolv.conf" "$MNT/etc/resolv.conf.allskyhub-orig"
fi
cp -L /etc/resolv.conf "$MNT/etc/resolv.conf"
printf '#!/bin/sh\nexit 101\n' > "$MNT/usr/sbin/policy-rc.d"
chmod +x "$MNT/usr/sbin/policy-rc.d"

echo "== agent $AGENT_VERSION: source (build only), uv, ZWO SDK, system files"
# Everything copied into the image belongs to root, never to the build user (tar keeps owners).
TAR_ROOT=(--owner=0 --group=0 --numeric-owner)
RELEASE="/opt/allskyhub-agent/releases/$AGENT_VERSION"
install -d "$MNT/tmp/allskyhub-src" "$MNT$RELEASE"
tar -C "$ROOT" "${TAR_ROOT[@]}" -cf - pyproject.toml uv.lock packages/protocol agent \
    server/pyproject.toml | tar -C "$MNT/tmp/allskyhub-src" -xf -
install -m 0755 "$UV_BIN" "$MNT/usr/local/bin/uv"
install -m 0755 "$OUT/cache/libASICamera2.so.1.39" "$MNT/usr/local/lib/libASICamera2.so.1.39"
ln -sf libASICamera2.so.1.39 "$MNT/usr/local/lib/libASICamera2.so"
install -D -m 0644 "$OUT/cache/asi-license.txt" "$MNT/usr/share/doc/libasicamera2/LICENSE"
install -m 0644 "$OUT/cache/asi.rules" "$MNT/etc/udev/rules.d/99-asi.rules"
# --no-overwrite-dir: /, /etc, /usr … keep owner and mode of the base image.
tar -C "$ROOT/image/files" "${TAR_ROOT[@]}" -cf - . | tar -C "$MNT" --no-overwrite-dir -xf -
echo "$VERSION" > "$MNT/etc/allskyhub-image-version"
# A unit's EnvironmentFile without "-" must exist, or the unit never starts.
sed -n 's/^EnvironmentFile=\([^-].*\)$/\1/p' "$ROOT"/image/files/etc/systemd/system/* |
    while read -r f; do
        [ -f "$MNT$f" ] || { echo "missing $f (EnvironmentFile of a unit)"; exit 1; }
    done

echo "== chroot"
chroot "$MNT" /usr/bin/env RELEASE="$RELEASE" AGENT_VERSION="$AGENT_VERSION" \
    /bin/bash -euxo pipefail <<'CHROOT'
export DEBIAN_FRONTEND=noninteractive LC_ALL=C.UTF-8
apt-get update
apt-get install -y --no-install-recommends \
    python3 ffmpeg rpicam-apps-lite libusb-1.0-0 dnsmasq-base polkitd iw nftables \
    unattended-upgrades
ldconfig

# Service user: cameras (video), its state in /var/lib/allskyhub-agent.
useradd --system --home-dir /var/lib/allskyhub-agent --no-create-home \
    --shell /usr/sbin/nologin --groups video allskyhub
install -d -o allskyhub -g allskyhub -m 0750 /var/lib/allskyhub-agent /var/lib/allskyhub-agent/data

# Agent venv from the pinned lock, not editable: the release directory is self-contained, so
# an updater can replace it as a whole (SPEC §8).
cd /tmp/allskyhub-src
export UV_PYTHON_DOWNLOADS=never UV_CACHE_DIR=/tmp/uv-cache UV_PROJECT_ENVIRONMENT="$RELEASE/.venv"
uv venv --python /usr/bin/python3 "$RELEASE/.venv"
uv sync --frozen --no-dev --no-editable --package allskyhub-agent
ln -sfn "releases/$AGENT_VERSION" /opt/allskyhub-agent/current
cd /
rm -rf /tmp/uv-cache /tmp/allskyhub-src /usr/local/bin/uv
[ "$(/opt/allskyhub-agent/current/.venv/bin/allskyhub-agent --version)" = "$AGENT_VERSION" ]

apt-get clean
rm -rf /var/lib/apt/lists/*

systemctl enable allskyhub-firstboot.service allskyhub-setupfile.service \
    allskyhub-agent.service allskyhub-wifi-country.path nftables.service \
    allskyhub-updater.timer
# The agent announces itself over mDNS (SPEC §7.2) with its own responder; avahi would hold
# UDP 5353 and answer for the same host.
systemctl mask avahi-daemon.service avahi-daemon.socket

# Nothing may belong to a user that does not exist on the camera (e.g. the build user).
orphans=$(find / -xdev \( -nouser -o -nogroup \) -print)
[ -z "$orphans" ] || { echo "files without owner on the camera:"; echo "$orphans"; exit 1; }

# Self-test without hardware or network: the simulated camera through the whole pipeline.
runuser -u allskyhub -- /opt/allskyhub-agent/current/.venv/bin/allskyhub-agent \
    --sim --lat 48.14 --lon 14.39 run --frames 3 --data /tmp/selftest \
    --start 2026-10-06T22:00:00+02:00
rm -rf /tmp/selftest
CHROOT

echo "== update bundle (SPEC §8): the release directory as the camera runs it"
tar -C "$MNT/opt/allskyhub-agent/releases" --owner=0 --group=0 --numeric-owner \
    -cJf "$OUT/allskyhub-agent-$AGENT_VERSION-arm64.tar.xz" "$AGENT_VERSION"

echo "== boot order"
# No ordering cycles: systemd would break one by dropping a unit, NetworkManager for example.
# cloud-init's generator enables cloud-init.target at boot, so it is linked here for the check.
GEN_LINK="$MNT/etc/systemd/system/multi-user.target.wants/cloud-init.target"
ln -s /usr/lib/systemd/system/cloud-init.target "$GEN_LINK"
BOOT_LOG=$(SYSTEMD_LOG_LEVEL=debug systemd-analyze --root="$MNT" verify --man=no \
    multi-user.target 2>&1 || true)
rm -f "$GEN_LINK"
if grep -qi "ordering cycle" <<<"$BOOT_LOG"; then
    grep -i -A3 "ordering cycle" <<<"$BOOT_LOG"
    exit 1
fi
for unit in NetworkManager.service allskyhub-firstboot.service allskyhub-agent.service; do
    grep -q "Installed new job $unit/start" <<<"$BOOT_LOG" || { echo "$unit not started at boot"; exit 1; }
done

rm -f "$MNT/usr/sbin/policy-rc.d" "$MNT/etc/resolv.conf"
if [ -e "$MNT/etc/resolv.conf.allskyhub-orig" ] || [ -L "$MNT/etc/resolv.conf.allskyhub-orig" ]; then
    mv "$MNT/etc/resolv.conf.allskyhub-orig" "$MNT/etc/resolv.conf"
fi

echo "== finish"
cleanup
trap - EXIT
xz -T0 -6 -f "$IMG"
(cd "$OUT" && sha256sum "$(basename "$IMG").xz" > "$(basename "$IMG").xz.sha256")
ls -la "$IMG.xz"
echo "version $VERSION, agent $AGENT_VERSION"
