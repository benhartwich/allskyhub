# allskyhub app

Milestone M3: Flutter app for Android and iOS (pairing, live view, settings, push alerts), built
with Codemagic (`codemagic.yaml` in the repository root). Licensed Apache-2.0 so it can be
distributed through the app stores.

## State

- Sign-in against a hub (default `https://allskyhub.org`, own hub selectable), token in the
  Keychain/Keystore (`flutter_secure_storage`)
- Camera list with thumbnails and online status, pull to refresh
- Camera page with live view: polls the latest image with `live=true`, so the hub asks the camera
  for every frame while the page is open (SPEC §6.5); status, removing the camera
- Pairing by typing the code (SPEC §6.2 step 3, fallback)

Next: onboarding via the camera's setup hotspot / local setup API (SPEC §6.2 step 1, §7) that
reads the pairing code without typing, push alerts, settings.

The app talks to the hub's app API v1 (`server/src/allskyhub_server/api/app.py`; OpenAPI at
`/api/docs` on a dev hub).

## Development

```bash
cd app
flutter pub get
flutter run                     # device or emulator
dart format lib test && flutter analyze && flutter test
```

Without a local Flutter SDK, the Docker image works:

```bash
docker run --rm -v "$PWD":/work -w /work/app ghcr.io/cirruslabs/flutter:stable flutter test
```

IDs: Android `applicationId` and iOS bundle id are `org.allskyhub.app`.
