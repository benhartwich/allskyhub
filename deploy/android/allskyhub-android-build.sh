#!/bin/bash
# Android builds of the app on the hub's server (deploy/android/README.md), run as root.
#
#   allskyhub-android-build.sh [--force]   test build, by allskyhub-android-build.timer
#                                          every ten minutes when app/ changed on
#                                          origin/main: a release APK (arm64) signed with
#                                          the server's test key, published under /app/android/
#   allskyhub-android-build.sh --release   store build, by allskyhub-android-release.path
#                                          when the admin page asks: an App Bundle signed
#                                          with the hub's upload key, then sent to Google
#                                          Play's internal test track by the hub
#                                          (allskyhub-server play-upload)
#
# Flutter and Gradle run in a container (ghcr.io/cirruslabs/flutter), limited in CPU and
# memory so the hub keeps its share. dl.google.com does not answer HTTP/1.1 from here, so
# Google's Maven repository goes through maven-google-proxy.py.
set -euo pipefail

main() {
    local BASE=${ALLSKYHUB_ANDROID_BASE:-/var/lib/allskyhub-android}
    # Everything root writes or trusts lives under BASE, which only root can change. The
    # hub's data folder is the hub's: whatever root finds there may be a symlink the hub
    # planted, so root only reads a request and the upload key there, the key as the hub.
    local KEYS=${ALLSKYHUB_ANDROID_KEYS:-$BASE/keys}
    local OUT=${ALLSKYHUB_ANDROID_OUT:-$BASE/android-test}
    local HUB_USER=${ALLSKYHUB_HUB_USER:-allskyhub-server}
    local REPO_URL=${ALLSKYHUB_REPO_URL:-https://github.com/benhartwich/allskyhub.git}
    # Pinned by digest (like the SHA-pinned actions): the build sees the test key, so a
    # changed image must not get there unreviewed. Update: docker pull the tag, put its
    # RepoDigest here. Flutter 3.44.0.
    local IMAGE=${ALLSKYHUB_FLUTTER_IMAGE:-ghcr.io/cirruslabs/flutter@sha256:46691e311715845de03a3ba4753a475476936805b29431b1f00f1816981033f8}
    local BRANCH=${ALLSKYHUB_ANDROID_BRANCH:-main}  # another only for trying a branch out
    local PROXY_HOST=172.17.0.1 PROXY_PORT=8471
    local HERE
    HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
    local force=${1:-}
    # The hub only drops a request into SPOOL (its own folder). Root's answers (state, log,
    # latest.json, the App Bundles) go to RELEASE, which the hub can read but not change.
    local SPOOL=${ALLSKYHUB_RELEASE_SPOOL:-/var/lib/allskyhub-server/android-release}
    local RELEASE=${ALLSKYHUB_RELEASE_OUT:-$BASE/release}
    local STORE=${ALLSKYHUB_STORE_DIR:-/var/lib/allskyhub-server/store}
    local REPO=${ALLSKYHUB_DEPLOY_REPO:-/opt/allskyhub-server}
    local ENV_FILE=${ALLSKYHUB_ENV_FILE:-/etc/allskyhub-server/allskyhub-server.env}

    install -d -m 755 "$BASE"
    mkdir -p "$BASE/maven-google"
    exec 9>"$BASE/.lock"
    if [[ $force == --release ]]; then
        # The spool belongs to the hub and is untrusted: only whether a request is there
        # counts. A test build may be running; wait for it.
        [[ -e $SPOOL/request || -L $SPOOL/request ]] || exit 0
        runuser -u "$HUB_USER" -- rm -f -- "$SPOOL/request"
        install -d -m 755 "$RELEASE"
        rm -f -- "$RELEASE/log"
        exec >> "$RELEASE/log" 2>&1
        chmod 644 "$RELEASE/log"
        answer "$RELEASE" state running
        set -E  # the ERR trap also in functions
        trap 'answer "$RELEASE" state failed; echo "FEHLER beim Bauen."' ERR
        flock -w 10800 9
    else
        flock -n 9 || exit 0
    fi

    local SRC=$BASE/src
    if [[ ! -d $SRC/.git ]]; then
        git clone --quiet "$REPO_URL" "$SRC"
    fi
    git -C "$SRC" fetch --quiet origin "$BRANCH"
    local tree
    tree=$(git -C "$SRC" rev-parse FETCH_HEAD:app)
    # No published build yet (also right after the move into BASE): build even if app/ is
    # unchanged.
    if [[ $force == "" && -f $OUT/latest.json && -f $BASE/last-tree &&
        $(cat "$BASE/last-tree") == "$tree" ]]; then
        exit 0
    fi
    git -C "$SRC" checkout --quiet --force --detach FETCH_HEAD
    git -C "$SRC" clean --quiet -fdx -- app
    local commit build version
    commit=$(git -C "$SRC" rev-parse --short=7 HEAD)
    build=$(git -C "$SRC" rev-list --count HEAD)
    version=$(sed -n 's/^version: *\([0-9.]*\).*/\1/p' "$SRC/app/pubspec.yaml")
    echo "Build $build ($version, $commit)"

    python3 "$HERE/maven-google-proxy.py" "$PROXY_HOST" "$PROXY_PORT" "$BASE/maven-google" &
    PROXY_PID=$!  # global: the EXIT trap runs after main() has returned
    trap 'kill "$PROXY_PID" 2>/dev/null || true; rm -rf "${KEY_TMP:-/nonexistent}"' EXIT

    if [[ $force == --release ]]; then
        release_build
        return
    fi
    move_test_key /var/lib/allskyhub-server/android-keys "$KEYS"
    test_key "$KEYS" "$IMAGE"

    docker run --rm --name allskyhub-android-build --cpus 6 --memory 8g \
        -v "$SRC:/work" \
        -v allskyhub-android-pub-cache:/root/.pub-cache \
        -v allskyhub-android-gradle:/root/.gradle \
        -v "$HERE/maven-google.init.gradle:/root/.gradle/init.d/maven-google.gradle:ro" \
        -v "$HERE/gradle.properties:/root/.gradle/gradle.properties:ro" \
        -v "$KEYS/test.keystore:/keys/test.keystore:ro" \
        --env-file "$KEYS/keystore.env" \
        -e ALLSKYHUB_KEYSTORE=/keys/test.keystore \
        -e ALLSKYHUB_CHANNEL=test \
        -e ALLSKYHUB_MAVEN_GOOGLE="http://$PROXY_HOST:$PROXY_PORT/" \
        -w /work/app "$IMAGE" \
        sh -c "flutter pub get && flutter build apk --release --target-platform android-arm64 \
            --build-number=$build --dart-define=ALLSKYHUB_CHANNEL=test \
            --dart-define=ALLSKYHUB_BUILD=$build"

    local apk=$SRC/app/build/app/outputs/flutter-apk/app-release.apk
    local name=allskyhub-test-$build.apk
    install -d -m 755 "$OUT"
    install -m 644 "$apk" "$OUT/$name"
    local size sha
    size=$(stat -c %s "$apk")
    sha=$(sha256sum "$apk" | cut -d' ' -f1)
    printf '{"build": %d, "version": "%s", "commit": "%s", "built_at": "%s", "size": %d, "sha256": "%s", "file": "%s"}\n' \
        "$build" "$version" "$commit" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$size" "$sha" "$name" \
        | answer_stdin "$OUT" latest.json
    # The three newest APKs stay, so a download that started before still finishes.
    # shellcheck disable=SC2012  # our own file names: no spaces or newlines
    ls -1t "$OUT"/allskyhub-test-*.apk | tail -n +4 | xargs -r rm -f
    echo "$tree" > "$BASE/last-tree"
    echo "Published $name ($size bytes)"
}

# The store build: an App Bundle signed with the hub's upload key (admin/stores.py keeps
# it as JSON with the PKCS#12 file in base64), handed to the hub for the upload to Play.
# Uses the variables of main().
release_build() {
    [[ -f $STORE/android_upload.secret ]] || {
        echo 'FEHLER: Kein Upload-Schlüssel, erst auf der Admin-Seite „Prüfen und einrichten“.'
        answer "$RELEASE" state failed
        return
    }
    KEY_TMP=$(mktemp -d)  # global for the EXIT trap
    chmod 700 "$KEY_TMP"
    # Read as the hub: the key is the hub's, and a symlink there must not reach root's files.
    runuser -u "$HUB_USER" -- cat -- "$STORE/android_upload.secret" > "$KEY_TMP/secret.json"
    python3 - "$KEY_TMP/secret.json" "$KEY_TMP" <<'PY'
import base64, json, os, sys
key = json.load(open(sys.argv[1]))
os.umask(0o077)
with open(os.path.join(sys.argv[2], "upload.p12"), "wb") as f:
    f.write(base64.b64decode(key["p12"]))
with open(os.path.join(sys.argv[2], "keystore.env"), "w") as f:
    f.write("ALLSKYHUB_KEYSTORE_PASSWORD=%s\nALLSKYHUB_KEY_ALIAS=upload\n" % key["password"])
    f.write("ALLSKYHUB_KEY_PASSWORD=%s\n" % key["password"])
PY
    docker run --rm --name allskyhub-android-release --cpus 6 --memory 8g \
        -v "$SRC:/work" \
        -v allskyhub-android-pub-cache:/root/.pub-cache \
        -v allskyhub-android-gradle:/root/.gradle \
        -v "$HERE/maven-google.init.gradle:/root/.gradle/init.d/maven-google.gradle:ro" \
        -v "$HERE/gradle.properties:/root/.gradle/gradle.properties:ro" \
        -v "$KEY_TMP/upload.p12:/keys/upload.p12:ro" \
        --env-file "$KEY_TMP/keystore.env" \
        -e ALLSKYHUB_KEYSTORE=/keys/upload.p12 \
        -e ALLSKYHUB_MAVEN_GOOGLE="http://$PROXY_HOST:$PROXY_PORT/" \
        -w /work/app "$IMAGE" \
        sh -c "flutter pub get && flutter build appbundle --release --build-number=$build"
    rm -rf "$KEY_TMP"

    local aab=$SRC/app/build/app/outputs/bundle/release/app-release.aab
    local name=allskyhub-$build.aab
    install -m 644 "$aab" "$RELEASE/$name"
    printf '{"build": %d, "version": "%s", "commit": "%s", "built_at": "%s", "size": %d, "file": "%s"}\n' \
        "$build" "$version" "$commit" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(stat -c %s "$aab")" "$name" \
        | answer_stdin "$RELEASE" latest.json
    # shellcheck disable=SC2012  # our own file names: no spaces or newlines
    ls -1t "$RELEASE"/allskyhub-*.aab | tail -n +4 | xargs -r rm -f
    echo "$name gebaut ($(stat -c %s "$aab") Bytes). Hochladen zu Google Play …"

    trap - ERR
    local result=0
    sudo -u "$HUB_USER" bash -c \
        'set -a; . "$1"; exec "$2/.venv/bin/allskyhub-server" play-upload "$3"' \
        _ "$ENV_FILE" "$REPO" "$RELEASE/$name" || result=$?
    case $result in
        0) answer "$RELEASE" state ok ;;
        3) answer "$RELEASE" state built ;;  # the first upload goes by hand
        *) answer "$RELEASE" state failed ;;
    esac
}

answer() {  # answer <root-owned dir> <file> <text>: replaced atomically, readable by all
    printf '%s\n' "$3" | answer_stdin "$1" "$2"
}

answer_stdin() {  # answer_stdin <root-owned dir> <file>
    local tmp
    tmp=$(mktemp "$1/.$2.XXXXXX")
    cat > "$tmp"
    chmod 644 "$tmp"
    mv -f -- "$tmp" "$1/$2"
}

# Until 2026-10 the test key lay in the hub's data folder. It moves once, and only when that
# folder and both files are root's own, not symlinks: a replacement the hub put there is
# left alone and a new key is made.
move_test_key() {  # move_test_key <old> <new>
    local old=$1 new=$2 f
    [[ -e $new/test.keystore ]] && return
    [[ -d $old && ! -L $old && $(stat -c %u "$old") == 0 ]] || return 0
    for f in test.keystore keystore.env; do
        [[ -f $old/$f && ! -L $old/$f && $(stat -c %u "$old/$f") == 0 ]] || return 0
    done
    install -d -m 700 "$new"
    for f in test.keystore keystore.env; do
        cp -P -- "$old/$f" "$new/$f"
        if [[ -L $new/$f || ! -f $new/$f ]]; then  # swapped while copying
            rm -f -- "$new/test.keystore" "$new/keystore.env"
            return 0
        fi
        chmod 600 "$new/$f"
    done
    rm -rf -- "$old"
    echo "Test key moved to $new"
}

# The test key, made once: test builds update each other only when signed alike. Root
# only, in BASE: back up /var/lib/allskyhub-android/keys with the server.
test_key() {
    local keys=$1 image=$2
    [[ -f $keys/test.keystore && -f $keys/keystore.env ]] && return
    install -d -m 700 "$keys"
    local password
    password=$(head -c 24 /dev/urandom | base64 | tr -d '/+=')
    docker run --rm -v "$keys:/keys" "$image" keytool -genkeypair -noprompt \
        -keystore /keys/test.keystore -storetype PKCS12 -alias test -keyalg RSA -keysize 4096 \
        -validity 10000 -dname "CN=allskyhub test builds" \
        -storepass "$password" -keypass "$password"
    chmod 600 "$keys/test.keystore"
    (umask 077; printf 'ALLSKYHUB_KEYSTORE_PASSWORD=%s\nALLSKYHUB_KEY_ALIAS=test\nALLSKYHUB_KEY_PASSWORD=%s\n' \
        "$password" "$password" > "$keys/keystore.env")
}

main "$@"
