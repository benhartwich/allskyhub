#!/bin/bash
# Android test build of the app on the hub's server (deploy/android/README.md), run as root
# by allskyhub-android-build.timer every ten minutes: when app/ changed on origin/main, a
# release APK (arm64) signed with the server's test key, published under /app/android/.
#
#   allskyhub-android-build.sh [--force]
#
# Flutter and Gradle run in a container (ghcr.io/cirruslabs/flutter), limited in CPU and
# memory so the hub keeps its share. dl.google.com does not answer HTTP/1.1 from here, so
# Google's Maven repository goes through maven-google-proxy.py.
set -euo pipefail

main() {
    local BASE=${ALLSKYHUB_ANDROID_BASE:-/var/lib/allskyhub-android}
    local KEYS=${ALLSKYHUB_ANDROID_KEYS:-/var/lib/allskyhub-server/android-keys}
    local OUT=${ALLSKYHUB_ANDROID_OUT:-/var/lib/allskyhub-server/app-builds/android-test}
    local HUB_USER=${ALLSKYHUB_HUB_USER:-allskyhub-server}
    local REPO_URL=${ALLSKYHUB_REPO_URL:-https://github.com/benhartwich/allskyhub.git}
    local IMAGE=${ALLSKYHUB_FLUTTER_IMAGE:-ghcr.io/cirruslabs/flutter:stable}
    local BRANCH=${ALLSKYHUB_ANDROID_BRANCH:-main}  # another only for trying a branch out
    local PROXY_HOST=172.17.0.1 PROXY_PORT=8471
    local HERE
    HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
    local force=${1:-}

    mkdir -p "$BASE" "$BASE/maven-google"
    exec 9>"$BASE/.lock"
    flock -n 9 || exit 0

    local SRC=$BASE/src
    if [[ ! -d $SRC/.git ]]; then
        git clone --quiet "$REPO_URL" "$SRC"
    fi
    git -C "$SRC" fetch --quiet origin "$BRANCH"
    local tree
    tree=$(git -C "$SRC" rev-parse FETCH_HEAD:app)
    if [[ $force != --force && -f $BASE/last-tree && $(cat "$BASE/last-tree") == "$tree" ]]; then
        exit 0
    fi
    git -C "$SRC" checkout --quiet --force --detach FETCH_HEAD
    git -C "$SRC" clean --quiet -fdx -- app
    local commit build version
    commit=$(git -C "$SRC" rev-parse --short=7 HEAD)
    build=$(git -C "$SRC" rev-list --count HEAD)
    version=$(sed -n 's/^version: *\([0-9.]*\).*/\1/p' "$SRC/app/pubspec.yaml")
    echo "Build $build ($version, $commit)"

    test_key "$KEYS" "$IMAGE"

    python3 "$HERE/maven-google-proxy.py" "$PROXY_HOST" "$PROXY_PORT" "$BASE/maven-google" &
    PROXY_PID=$!  # global: the EXIT trap runs after main() has returned
    trap 'kill "$PROXY_PID" 2>/dev/null || true' EXIT

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
    mkdir -p "$OUT"
    install -m 644 -o "$HUB_USER" -g "$HUB_USER" "$apk" "$OUT/$name"
    local size sha
    size=$(stat -c %s "$apk")
    sha=$(sha256sum "$apk" | cut -d' ' -f1)
    printf '{"build": %d, "version": "%s", "commit": "%s", "built_at": "%s", "size": %d, "sha256": "%s", "file": "%s"}\n' \
        "$build" "$version" "$commit" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$size" "$sha" "$name" \
        > "$OUT/latest.json.tmp"
    chown "$HUB_USER:$HUB_USER" "$OUT/latest.json.tmp"
    mv "$OUT/latest.json.tmp" "$OUT/latest.json"
    # The three newest APKs stay, so a download that started before still finishes.
    ls -1t "$OUT"/allskyhub-test-*.apk | tail -n +4 | xargs -r rm -f
    echo "$tree" > "$BASE/last-tree"
    echo "Published $name ($size bytes)"
}

# The test key, made once: test builds update each other only when signed alike. Root
# only; the hub's backup (rsync of /var/lib/allskyhub-server) keeps it.
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
