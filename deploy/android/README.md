# Android test builds on the hub's server

Signed release APKs of the app for testing, built on the hub's server instead of Codemagic
(which then only builds iOS and the store releases).

## How it works

- `allskyhub-android-build.timer` runs `allskyhub-android-build.sh` every ten minutes. When
  `app/` changed on `origin/main` (its own clone in `/var/lib/allskyhub-android/src`), it
  builds a release APK for arm64 in the container `ghcr.io/cirruslabs/flutter` (pinned by digest in the script), with
  at most 6 CPUs and 8 GB so the hub keeps its share. The first build takes a while (Gradle
  and Maven caches); later builds take minutes.
- Build number: the commit count of `main`. It is the APK's `versionCode` and goes into the
  app as `--dart-define=ALLSKYHUB_BUILD`, next to `ALLSKYHUB_CHANNEL=test`.
- The test app has the id `org.allskyhub.app.test` and the name "allskyhub Test", so it
  installs next to the store app.
- Signed with the server's test key, made on the first run:
  `/var/lib/allskyhub-android/keys/` (root only). **Add this folder to the server's
  backup**: an update installs only with the same key, and if it is lost, testers uninstall
  once. Until 2026-10 the key lay in `/var/lib/allskyhub-server/android-keys/`; the script
  moves it on its first run.
- Published in `/var/lib/allskyhub-android/android-test/` (root, 0755, files 0644) and
  served by the hub, which only reads it (setting `ALLSKYHUB_SERVER_ANDROID_DIR`):
  - `https://allskyhub.org/app/android/allskyhub-test.apk`, the newest APK (for the first
    install),
  - `https://allskyhub.org/app/android/test.json`, read by the test app at start; when the
    hub's build is newer, the camera list shows "Neue Testversion" with a button that
    downloads the APK in the browser.

## Store releases for Google Play

Admin page → App-Stores → "Android-Release bauen" leaves a request in
`/var/lib/allskyhub-server/android-release/`. `allskyhub-android-release.path` starts
`allskyhub-android-build.sh --release`:

- builds an App Bundle of `origin/main` (id `org.allskyhub.app`, build number = commit
  count of `main`) in the same container, after a test build that may be running;
- signs it with the hub's upload key (`/var/lib/allskyhub-server/store/android_upload.secret`,
  made by the hub on "Prüfen und einrichten"; Google re-signs with the app key, Play App
  Signing). The key goes into the container only for the build, from a temporary 0700
  folder;
- runs `allskyhub-server play-upload` as the hub's user, which sends it to the internal
  test track. As long as the app has never been published, Google takes draft releases
  only: then roll it out in the Play Console. The very first bundle of a new app must be
  uploaded by hand there; the admin page links the file.

Further apps (admin/stores.py `OTHER_APPS`) come from the request's first line,
`app=<key>`, each with its own clone, upload key and answer folder (`release-<key>`):

- `app=bortle`: Flutter, github.com/benhartwich/bortle-app, as above.
- `app=vinventory`: Expo (React Native), the private github.com/benhartwich/vinventory,
  cloned with the hub's read-only GitHub token (admin page → Version; it needs read
  access to that repository), handed to git in root's environment only. Built in
  `reactnativecommunity/react-native-android` (pinned by digest; 16 GB, four cores
  visible, two Gradle workers, Kotlin in-process, ABIs arm64-v8a and armeabi-v7a: a
  fresh build was killed at 8 and 12 GB; `GRADLE_USER_HOME` set back to `/root/.gradle`,
  which the image moves): `npm ci` at the root, `expo prebuild` and `gradlew bundleRelease` in
  `apps/mobile`. Its config plugin signs from `VINVENTORY_UPLOAD_*` and takes the
  version code from `VINVENTORY_VERSION_CODE` (the commit count).
- `app=currywursttest`: Flutter at the root of the private
  github.com/benhartwich/currywursttest-app, cloned with the token like Vinventory's
  (it needs read access to that repository too). Its Gradle file signs from
  `CURRYWURSTTEST_*`.
- `app=vinventory-test`: the same build as a test APK (`assembleRelease`,
  `VINVENTORY_VARIANT=test`): package `eu.vinventory.app.test`, "Vinventory Test", no
  Firebase, beside the store app. Google's universal APK of the store build sends users
  to Play (automatic protection), so this one is signed with a key of this server, made on
  the first run: `/var/lib/allskyhub-android/keys/vinventory-test.{p12,env}` (root only;
  back it up with the folder, an update installs only with the same key). Answers in
  `release-vinventory-test/`; the hub serves the newest APK at
  `/download/vinventory-test.apk` (public, rate-limited). Admin page → Apps → "Test-APK bauen".

The hub only drops `request` into its folder; root never writes there (anything in a
folder the hub owns could be a symlink). The answers, state (`running`, `ok`, `built` =
first upload by hand, `failed`), log, `latest.json` and the newest three App Bundles,
go to `/var/lib/allskyhub-android/release/` (root, 0755, files 0644, each replaced by
`mv`), which the hub reads for the admin page.

## Why a proxy for Google's Maven repository

From this server dl.google.com answers only HTTP/2 (HTTP/1.1 requests time out) and slowly.
Gradle speaks HTTP/1.1. `maven-google-proxy.py` serves the repository on `172.17.0.1:8471`
for the build container, downloads with `curl --http2`, checks each file against its
`.sha1` and keeps it in `/var/lib/allskyhub-android/maven-google`. `maven-google.init.gradle`
points Gradle there; `gradle.properties` allows long waits.

## Setup (once, as root)

```bash
cp /opt/allskyhub-server/deploy/systemd/allskyhub-android-build.{service,timer} /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now allskyhub-android-build.timer
systemctl start allskyhub-android-build.service   # first build now; journalctl -u … -f
```

For store releases from the admin page, once:

```bash
cp /opt/allskyhub-server/deploy/systemd/allskyhub-android-release.{path,service} /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now allskyhub-android-release.path
```

Root never writes into the hub's data folder `/var/lib/allskyhub-server` and never trusts
anything there: the hub owns it, so any name in it may be a symlink the hub planted. Root
only reads the release request and, as the hub's user, the upload key. Everything root
writes or relies on (source, caches, keys, builds, answers) lives in
`/var/lib/allskyhub-android`, which the hub can read but not change. The old
`/var/lib/allskyhub-server/app-builds/` can be deleted after the first build in the new place.

Force a build without an app change: `/opt/allskyhub-server/deploy/android/allskyhub-android-build.sh --force`.

## Install on a phone

Open `https://allskyhub.org/app/android/allskyhub-test.apk` on the phone, allow the browser
to install apps when Android asks, install. Later updates come through the hint in the app.
