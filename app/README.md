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
- Pairing without typing: the user enters the camera's address in the Wi-Fi, the app polls
  `GET http://<camera>:8080/api/setup` every 2 s, claims the code at the hub and waits until the
  camera reports `paired` (SPEC §6.2, §7). It refuses cameras that talk to another hub.
- New camera through its setup network (SPEC §7.1): the user joins `allskyhub-XXXX`, the app
  binds to that Wi-Fi (Android, `MainActivity.kt`), shows `/api/wifi/networks`, sends
  `/api/setup/network` with the app's hub, then finds the camera at home by DNS-SD
  `_allskyhub._tcp` (TXT `id`, SPEC §7.2, `nsd` plugin) and pairs it as above. `last_error` from
  a failed attempt is shown when the user reconnects to the setup network.
- Pairing by typing the code (fallback)

Plain HTTP to the camera needs: Android `network_security_config.xml` (cleartext allowed,
Android cannot limit it to private ranges), iOS `NSAllowsLocalNetworking` and
`NSLocalNetworkUsageDescription`.

Next: joining the setup network from the app (Android `WifiNetworkSpecifier`; iOS needs the
Hotspot Configuration entitlement), push alerts, settings.

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
