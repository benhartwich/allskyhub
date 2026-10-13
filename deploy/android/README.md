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
  `/var/lib/allskyhub-server/android-keys/` (root only; the server backup includes it). An
  update installs only with the same key: if it is lost, testers uninstall once.
- Published in `/var/lib/allskyhub-server/app-builds/android-test/` and served by the hub:
  - `https://allskyhub.org/app/android/allskyhub-test.apk`, the newest APK (for the first
    install),
  - `https://allskyhub.org/app/android/test.json`, read by the test app at start; when the
    hub's build is newer, the camera list shows "Neue Testversion" with a button that
    downloads the APK in the browser.

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

Force a build without an app change: `/opt/allskyhub-server/deploy/android/allskyhub-android-build.sh --force`.

## Install on a phone

Open `https://allskyhub.org/app/android/allskyhub-test.apk` on the phone, allow the browser
to install apps when Android asks, install. Later updates come through the hint in the app.
