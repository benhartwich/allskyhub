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
#                                          (allskyhub-server play-upload). A request
#                                          whose first line is "app=bortle" builds the
#                                          bortle app (github.com/benhartwich/bortle-app)
#                                          the same way, with its own upload key;
#                                          "app=vinventory" the Expo app of the private
#                                          github.com/benhartwich/vinventory (cloned with
#                                          the hub's GitHub token);
#                                          "app=vinventory-test" its test APK: package
#                                          eu.vinventory.app.test, signed with a key of
#                                          this server, published for the hub's
#                                          /download/vinventory-test.apk, no upload
#                                          "app=currywursttest" the Flutter app of the
#                                          private github.com/benhartwich/currywursttest-app
#
# Flutter and Gradle run in a container (ghcr.io/cirruslabs/flutter; for the Expo app
# reactnativecommunity/react-native-android), limited in CPU and memory so the hub keeps
# its share. dl.google.com does not answer HTTP/1.1 from here, so
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
    # Which app: allskyhub (app/ of this repository) or a further one the hub knows
    # (admin/stores.py OTHER_APPS), each with its clone, key, variables and answers.
    local app=allskyhub APP_DIR=app KEY_KIND=android_upload PREFIX=ALLSKYHUB
    local PACKAGE=org.allskyhub.app SRC=$BASE/src DEFINES=
    # flutter, or expo: React Native with expo prebuild and Gradle, in EXPO_IMAGE (pinned
    # like IMAGE; react-native-android v22.0: Node 22, JDK 17, Android SDK).
    local KIND=flutter PRIVATE='' VARIANT=''
    local EXPO_IMAGE=${ALLSKYHUB_EXPO_IMAGE:-reactnativecommunity/react-native-android@sha256:e817ea1959719c97c05adbc55348ebe21d286cec2caed1f18ad3c4b88c08cbf7}

    install -d -m 755 "$BASE"
    mkdir -p "$BASE/maven-google"
    exec 9>"$BASE/.lock"
    if [[ $force == --release ]]; then
        # The spool belongs to the hub and is untrusted: only whether a request is there
        # counts. A test build may be running; wait for it.
        [[ -e $SPOOL/request || -L $SPOOL/request ]] || exit 0
        # Read as the hub, briefly; only one exact line chooses the other app.
        local first
        first=$(timeout 5 runuser -u "$HUB_USER" -- head -n 1 -- "$SPOOL/request" || true)
        runuser -u "$HUB_USER" -- rm -f -- "$SPOOL/request"
        if [[ $first == app=bortle ]]; then
            app=bortle APP_DIR=. KEY_KIND=bortle_android_upload PREFIX=BORTLE
            PACKAGE=eu.bortle.app SRC=$BASE/src-bortle
            # Its Firebase identifiers (public; admin/stores.py fcm_defines): expanded by
            # the container's shell from --env-file, never pasted into the command.
            local f
            for f in PROJECT_ID SENDER_ID ANDROID_APP_ID ANDROID_API_KEY IOS_APP_ID IOS_API_KEY; do
                DEFINES+=" --dart-define=BORTLE_FCM_$f=\"\$BORTLE_FCM_$f\""
            done
            REPO_URL=${ALLSKYHUB_BORTLE_REPO_URL:-https://github.com/benhartwich/bortle-app.git}
            RELEASE=${ALLSKYHUB_BORTLE_RELEASE_OUT:-$BASE/release-bortle}
        elif [[ $first == app=vinventory || $first == app=vinventory-test ]]; then
            app=vinventory APP_DIR=apps/mobile KEY_KIND=vinventory_android_upload
            PREFIX=VINVENTORY_UPLOAD PACKAGE=eu.vinventory.app SRC=$BASE/src-vinventory
            KIND=expo PRIVATE=1
            REPO_URL=${ALLSKYHUB_VINVENTORY_REPO_URL:-https://github.com/benhartwich/vinventory.git}
            RELEASE=${ALLSKYHUB_VINVENTORY_RELEASE_OUT:-$BASE/release-vinventory}
            if [[ $first == app=vinventory-test ]]; then
                VARIANT=test PACKAGE=eu.vinventory.app.test RELEASE=$BASE/release-vinventory-test
            fi
        elif [[ $first == app=currywursttest ]]; then
            # Flutter at the root of a private repository, cloned with the token like
            # Vinventory's; its Gradle file signs from CURRYWURSTTEST_*.
            app=currywursttest APP_DIR=. KEY_KIND=currywursttest_android_upload
            PREFIX=CURRYWURSTTEST PACKAGE=com.currywursttest.app SRC=$BASE/src-currywursttest
            PRIVATE=1
            REPO_URL=${ALLSKYHUB_CURRYWURSTTEST_REPO_URL:-https://github.com/benhartwich/currywursttest-app.git}
            RELEASE=${ALLSKYHUB_CURRYWURSTTEST_RELEASE_OUT:-$BASE/release-currywursttest}
        fi
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

    if [[ -n $PRIVATE ]]; then
        # A private repository, with the hub's read-only GitHub token (admin page,
        # Version): read as the hub, handed to git in its environment only (root's own
        # process), never on a command line or into .git/config.
        local token
        token=$(timeout 5 runuser -u "$HUB_USER" -- cat -- "$STORE/github.secret" 2>/dev/null || true)
        if [[ ! $token =~ ^(github_pat_|ghp_)[A-Za-z0-9_]{20,255}$ ]]; then
            echo 'FEHLER: Das Repository ist privat; erst auf der Admin-Seite ein GitHub-Token mit Lesezugriff darauf hinterlegen.'
            answer "$RELEASE" state failed
            return
        fi
        export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=http.https://github.com/.extraheader
        GIT_CONFIG_VALUE_0="Authorization: Basic $(printf 'x-access-token:%s' "$token" | base64 -w0)"
        export GIT_CONFIG_VALUE_0
        unset token
    fi
    if [[ ! -d $SRC/.git ]]; then
        git clone --quiet "$REPO_URL" "$SRC"
    fi
    git -C "$SRC" fetch --quiet origin "$BRANCH"
    unset GIT_CONFIG_COUNT GIT_CONFIG_KEY_0 GIT_CONFIG_VALUE_0
    local tree=
    if [[ $force != --release ]]; then
        tree=$(git -C "$SRC" rev-parse FETCH_HEAD:app)
        # No published build yet (also right after the move into BASE): build even if
        # app/ is unchanged.
        if [[ $force == "" && -f $OUT/latest.json && -f $BASE/last-tree &&
            $(cat "$BASE/last-tree") == "$tree" ]]; then
            exit 0
        fi
    fi
    git -C "$SRC" checkout --quiet --force --detach FETCH_HEAD
    git -C "$SRC" clean --quiet -fdx -- "$APP_DIR"
    local commit build version
    commit=$(git -C "$SRC" rev-parse --short=7 HEAD)
    build=$(git -C "$SRC" rev-list --count HEAD)
    if [[ $KIND == expo ]]; then
        version=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["expo"]["version"])' \
            "$SRC/$APP_DIR/app.json")
        [[ $version =~ ^[0-9]+(\.[0-9]+){0,2}$ ]] || version=
    else
        version=$(sed -n 's/^version: *\([0-9.]*\).*/\1/p' "$SRC/$APP_DIR/pubspec.yaml")
    fi
    echo "$app: build $build ($version, $commit)"

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
# Uses the variables of main(); the app's Gradle file reads <PREFIX>_KEYSTORE and the rest.
release_build() {
    KEY_TMP=$(mktemp -d)  # global for the EXIT trap
    chmod 700 "$KEY_TMP"
    if [[ -n $VARIANT ]]; then
        # The test APK: a key of this server (root only, in KEYS, like allskyhub's test
        # key), no Firebase (the test package is not registered there).
        variant_key
        cp -- "$KEYS/$app-test.p12" "$KEY_TMP/upload.p12"
        cp -- "$KEYS/$app-test.env" "$KEY_TMP/keystore.env"
    else
        if [[ ! -f $STORE/$KEY_KIND.secret ]]; then
            echo 'FEHLER: Kein Upload-Schlüssel, erst auf der Admin-Seite „Prüfen und einrichten“.'
            answer "$RELEASE" state failed
            return
        fi
        # Called plainly: in an `||` or `if` errexit and the ERR trap would be off inside.
        store_key
    fi
    signed_build
}

# The hub's upload key (and the app's Firebase identifiers) into KEY_TMP.
store_key() {
    # Read as the hub: the key is the hub's, and a symlink there must not reach root's files.
    runuser -u "$HUB_USER" -- cat -- "$STORE/$KEY_KIND.secret" > "$KEY_TMP/secret.json"
    # A further app's Firebase identifiers, if the admin page has them (fcm_app_<app>.json).
    timeout 5 runuser -u "$HUB_USER" -- cat -- "$STORE/fcm_app_$app.json" \
        > "$KEY_TMP/fcm.json" 2>/dev/null || true
    # The Firebase names follow the app's key, as in its Codemagic group (stores.fcm_defines:
    # BORTLE_FCM_*, VINVENTORY_FCM_*), whatever its signing variables are called.
    python3 - "$KEY_TMP/secret.json" "$KEY_TMP" "$PREFIX" "$KEY_TMP/fcm.json" "${app^^}" <<'PY'
import base64, json, os, re, sys
key = json.load(open(sys.argv[1]))
prefix = sys.argv[3]
os.umask(0o077)
with open(os.path.join(sys.argv[2], "upload.p12"), "wb") as f:
    f.write(base64.b64decode(key["p12"]))
with open(os.path.join(sys.argv[2], "keystore.env"), "w") as f:
    f.write("%s_KEYSTORE_PASSWORD=%s\n%s_KEY_ALIAS=upload\n" % (prefix, key["password"], prefix))
    f.write("%s_KEY_PASSWORD=%s\n" % (prefix, key["password"]))
    # The hub wrote these; only plain identifiers pass (admin/stores.py FCM_ID).
    try:
        fcm = json.load(open(sys.argv[4]))
    except (OSError, ValueError):
        fcm = {}
    fields = ("project_id", "sender_id", "android_app_id", "android_api_key",
              "ios_app_id", "ios_api_key")
    for name in fields if isinstance(fcm, dict) else ():
        value = fcm.get(name)
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9:._-]{1,200}", value):
            f.write("%s_FCM_%s=%s\n" % (sys.argv[5], name.upper(), value))
PY
}

# A further app's test key, made once: test APKs update each other only when signed alike.
# Back up /var/lib/allskyhub-android/keys with the server.
variant_key() {
    [[ -f $KEYS/$app-test.p12 && -f $KEYS/$app-test.env ]] && return
    install -d -m 700 "$KEYS"
    local password
    password=$(head -c 24 /dev/urandom | base64 | tr -d '/+=')
    # The password through the environment, not on a command line (/proc/*/cmdline).
    KS_PASS=$password docker run --rm -e KS_PASS -v "$KEYS:/keys" "$EXPO_IMAGE" \
        keytool -genkeypair -noprompt \
        -keystore "/keys/$app-test.p12" -storetype PKCS12 -alias test -keyalg RSA \
        -keysize 4096 -validity 10000 -dname "CN=$app test builds" \
        -storepass:env KS_PASS -keypass:env KS_PASS
    chmod 600 "$KEYS/$app-test.p12"
    (umask 077; printf '%s_KEYSTORE_PASSWORD=%s\n%s_KEY_ALIAS=test\n%s_KEY_PASSWORD=%s\n' \
        "$PREFIX" "$password" "$PREFIX" "$PREFIX" "$password" > "$KEYS/$app-test.env")
    echo "Test key made: $KEYS/$app-test.p12"
}

# Gradle with the key in KEY_TMP: an App Bundle for the store, or the test APK.
signed_build() {
    # React Native's build needs more than Flutter's: a fresh one was killed at 8 and 12 GB
    # (2026-10-29: Gradle, Kotlin daemon, R8 and clang for every core the container saw).
    # 16 GB, four cores visible, two Gradle workers, Kotlin in Gradle's own process, the
    # two phone ABIs: 12–17 minutes in trials here.
    local limits=(--cpus 6 --memory 8g)
    [[ $KIND == expo ]] && limits=(--cpus 4 --cpuset-cpus 0-3 --memory 16g)
    local run=(docker run --rm --name allskyhub-android-release "${limits[@]}"
        -v "$SRC:/work"
        -v allskyhub-android-gradle:/root/.gradle
        -v "$HERE/maven-google.init.gradle:/root/.gradle/init.d/maven-google.gradle:ro"
        -v "$HERE/gradle.properties:/root/.gradle/gradle.properties:ro"
        -v "$KEY_TMP/upload.p12:/keys/upload.p12:ro"
        --env-file "$KEY_TMP/keystore.env"
        -e "${PREFIX}_KEYSTORE=/keys/upload.p12"
        -e ALLSKYHUB_MAVEN_GOOGLE="http://$PROXY_HOST:$PROXY_PORT/")
    local aab task=bundleRelease
    [[ -n $VARIANT ]] && task=assembleRelease
    if [[ $KIND == expo ]]; then
        # The workspace root installs (npm workspaces), the app's native project is made
        # fresh by expo prebuild; its config plugin signs from <PREFIX>_* and takes the
        # version code from VINVENTORY_VERSION_CODE.
        # The image points GRADLE_USER_HOME elsewhere; back to /root/.gradle, where the
        # cache volume, the Maven proxy's init script and gradle.properties are.
        "${run[@]}" -v allskyhub-android-npm:/root/.npm -e CI=1 -e GRADLE_USER_HOME=/root/.gradle \
            -e "VINVENTORY_VERSION_CODE=$build" -e "VINVENTORY_VARIANT=$VARIANT" \
            -w /work "$EXPO_IMAGE" \
            sh -c "npm ci --no-audit --no-fund && cd $APP_DIR \
                && npx expo prebuild --platform android --clean --no-install \
                && cd android && ./gradlew --no-daemon --max-workers=2 \
                    -Pkotlin.compiler.execution.strategy=in-process \
                    -PreactNativeArchitectures=arm64-v8a,armeabi-v7a $task"
        aab=$SRC/$APP_DIR/android/app/build/outputs/bundle/release/app-release.aab
    else
        "${run[@]}" -v allskyhub-android-pub-cache:/root/.pub-cache \
            -w "/work/$APP_DIR" "$IMAGE" \
            sh -c "flutter pub get && flutter build appbundle --release --build-number=$build$DEFINES"
        aab=$SRC/$APP_DIR/build/app/outputs/bundle/release/app-release.aab
    fi
    rm -rf "$KEY_TMP"
    if [[ -n $VARIANT ]]; then
        publish_variant "$SRC/$APP_DIR/android/app/build/outputs/apk/release/app-release.apk"
        return
    fi

    local name=$app-$build.aab
    install -m 644 "$aab" "$RELEASE/$name"
    printf '{"build": %d, "version": "%s", "commit": "%s", "built_at": "%s", "size": %d, "file": "%s"}\n' \
        "$build" "$version" "$commit" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(stat -c %s "$aab")" "$name" \
        | answer_stdin "$RELEASE" latest.json
    # shellcheck disable=SC2012  # our own file names: no spaces or newlines
    ls -1t "$RELEASE/$app"-*.aab | tail -n +4 | xargs -r rm -f
    echo "$name gebaut ($(stat -c %s "$aab") Bytes). Hochladen zu Google Play …"

    trap - ERR
    local result=0
    sudo -u "$HUB_USER" bash -c \
        'set -a; . "$1"; exec "$2/.venv/bin/allskyhub-server" play-upload --package "$4" "$3"' \
        _ "$ENV_FILE" "$REPO" "$RELEASE/$name" "$PACKAGE" || result=$?
    case $result in
        0) answer "$RELEASE" state ok ;;
        3) answer "$RELEASE" state built ;;  # the first upload goes by hand
        *) answer "$RELEASE" state failed ;;
    esac
}

# The test APK into RELEASE (release-<app>-test), where the hub reads it; no upload.
publish_variant() {  # publish_variant <apk>
    local apk=$1 name=$app-test-$build.apk size sha
    size=$(stat -c %s "$apk")
    sha=$(sha256sum "$apk" | cut -d' ' -f1)
    install -m 644 "$apk" "$RELEASE/$name"
    printf '{"build": %d, "version": "%s", "commit": "%s", "built_at": "%s", "size": %d, "sha256": "%s", "file": "%s"}\n' \
        "$build" "$version" "$commit" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$size" "$sha" "$name" \
        | answer_stdin "$RELEASE" latest.json
    # shellcheck disable=SC2012  # our own file names: no spaces or newlines
    ls -1t "$RELEASE/$app"-test-*.apk | tail -n +4 | xargs -r rm -f
    trap - ERR
    answer "$RELEASE" state ok
    echo "$name gebaut ($size Bytes)."
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
