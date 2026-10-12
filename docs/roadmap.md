# Roadmap

What is done and what comes next, in order. Each item becomes one small PR based on `main`;
PRs are merged as soon as CI is green. Owners: **agent** = camera agent, protocol, image
(session "allsky" on the test Pi); **hub** = server and app (session "allskyhub" on
postkasten.cloud). Changes to `packages/protocol` and `docs/SPEC.md` have one owner per PR.

## Done (October 2026)

- M0 core: protocol, sun, day/night, auto exposure, storage, simulated camera.
- M1 cameras: ZWO (tested on an ASI678MC), Raspberry Pi cameras via rpicam-still (only
  tested with a fake), local web UI with focus helper.
- M2 hub: pairing, device API, live and latest image, frame archive, invitations only,
  imprint and privacy policy; live at https://allskyhub.org.
- M3 app (Android first): pairing without typing, setup Wi-Fi, location and time zone,
  camera status, gallery, night products.
- M4 products: keogram, startrails, timelapse; announced and uploaded to the hub.
- M5 part 1: Raspberry Pi image (Pi 4/5) built in CI.

## Next

| # | Item | Owner | Notes |
|---|---|---|---|
| 1 | ~~Meteor detection → `event` (meteor) with image~~ done (v1) | agent | aircraft/satellite great-circle filter and star veto need the fisheye calibration (#8) |
| 2 | ~~`set_settings` command~~ done | agent + hub | agent, hub API, web and app screen |
| 3 | ~~Free-space retention (keep ≥ 10 % free)~~ done | agent | |
| 4 | ~~Wi-Fi country from the app~~ done | agent | root path unit in the image |
| 5 | ~~Lightning, sky quality and night cloud cover (`status.sky`), aurora~~ done; NLC done in #8 | agent | ports of the existing Allsky modules; NLC needs the sunward band, i.e. the image orientation from #8, and the module only found false alarms at 48° N without it; lightning v1 detects only, a short "storm exposure" comes later; day cloud cover needs a method that works without an IR cut filter |
| 6 | Push notifications (FCM) for events | hub | hub side done (off until a Firebase service account is configured); app side needs the Firebase project (Benjamin) |
| 7 | ~~Signed agent updates with rollback (M5 part 2)~~ done | agent | port of myboxi's updater; needs the signing keys (docs/updates.md) before cameras go out |
| 8 | ~~Hot pixels without darks, sky mask, plate-solve orientation, event azimuths, NLC, aurora pole sector~~ (all done) | agent | |
| 9 | ~~Public sky page per camera~~ | hub | done: opt-in, random link, web + app API |
| 10 | ~~Self-service account: change password, delete account~~ | hub | done: web UI, app API and app |
| 11 | ~~Hub backups: database dump~~ | hub | daily dump done; the external rsync of /var/lib/allskyhub-server is Benjamin's |
| 12 | ~~Hub monitoring: health check, disk space, certificate, backup age~~ | hub | done (ntfy topic from Benjamin); camera offline is push (#6) |
| 13 | ~~Web UI: gallery and night products like in the app~~ | hub | done |
| 14 | ~~App: "Standort ändern" and camera settings via `set_settings`~~ | hub | done |
| 15 | E-mail from the hub: invitations, password reset, alerts as an alternative to push | hub | the hub sends no mail yet; invitation links are passed on by hand (`allskyhub-server invite`). Needs an SMTP account (Benjamin) |
| 16 | Public sky page like astronomy.garden, manageable on the hub and on the camera | hub + agent | hub sections done (#this PR); constellation overlay, `GET/PUT /device/v1/public` and the camera's local UI follow (SPEC PR) |
| 17 | Own domain for a public page (e.g. sky.example.org): the owner sets a CNAME to allskyhub.org, presses "Domain übernehmen", the hub checks DNS, gets a Let's Encrypt certificate (HTTP-01) and serves the page there | hub | needs a certificate path that does not stop nginx (today certbot standalone) and an nginx catch-all; Benjamin decides before it goes live |

## Local web portal (agent)

Benjamin, 2026-10-12: today the local UI (SPEC §7) shows only the live image and the
focus helper. It should do on the camera what the Allsky WebUI does, adapted to
allskyhub: everything works offline on the local network, and anything that is also in
the hub stays in step with it. The local work comes first; the Flutter app follows after
it. Order:

| # | Item | Owner | Notes |
|---|---|---|---|
| L1 | Local login: hub SSO + offline code | agent + hub | spec PR by the hub session; until then lighttpd basic auth on the test Pi |
| L2 | `packages/design` + layout with navigation (Live, Archive, Events, Sky, Settings, System), mobile-first, German/English | agent + hub | tokens from #76; red night mode so the screen does not ruin dark adaptation |
| L3 | Live: image with zoom/fullscreen, frame data (time, exposure, gain, sensor °C, sun elevation, day/night), loop of the last hour | agent | Allsky "Live View" |
| L4 | Archive: list of nights, image grid with thumbnails, lightbox, download; keogram, startrails, timelapse player per night | agent | Allsky "Images"; data in `ImageStore`, products from `products/build.py` |
| L5 | Events: meteors, aurora, lightning, NLC with image, time, azimuth; filter by type and night | agent | Allsky "Meteors" and the own modules; data from `detect/events.py` |
| L6 | Sky: charts per night for sky quality (SQM/Bortle), cloud cover, exposure/gain, sensor temperature | agent | Allsky "Charts"; `status.sky` history |
| L7 | Camera settings: location (map), time zone, day/night delay, exposure and gain limits, white balance, flip/rotation, JPEG quality | agent | Allsky "Allsky Settings" reduced to what the agent uses; extends `AgentSettings`, same validation as `set_settings`, the hub sees changes in `status` |
| L8 | Detection and image setup: detectors on/off and sensitivity, sky-mask editor, orientation check (north, plate-solve result) | agent | replaces Allsky's module manager; no free-form modules |
| L9 | Night products: which ones, timelapse fps/quality, retention | agent | |
| L10 | Public page management (`GET/PUT /device/v1/public`) | agent + hub | spec PR by the hub session; read-only when the hub is unreachable |
| L11 | Overlay: on/off and fields (time, exposure, temperature, stars/constellations) | agent | simple version of the Allsky overlay editor |
| L12 | System: CPU temperature/load, RAM, disk, uptime, camera, hub connection; restart agent, reboot, shut down | agent | Allsky "System"; needs a narrow root path (polkit) like the Wi-Fi country |
| L13 | Network: Wi-Fi status and change, LAN info | agent | setup-mode code exists; off with `--network none` |
| L14 | Updates: version, channel, check/install, rollback | agent | updater from #7 |
| L15 | Logs and support: agent journal, download a support bundle; backup/restore of settings | agent | Allsky "Support" |

Not ported on purpose: the free-form module manager and file editor (fixed detectors
instead), the website upload/remote server (the hub and its public page do that).

## Needs Benjamin / hardware

- First boot of the image on the spare Pi 4 (SD card ordered); test with `"camera": "sim"`.
- Raspberry Pi HQ camera (CS mount) to test the rpi-hq profile; fits the Altair 1.55 mm.
- Apple developer account (open), Play Console via WebInx.
- Housing: Daniel Nimmervoll's design.
- Update signing keys: create the main and emergency key pair, commit the public keys, set
  the secret `ALLSKYHUB_UPDATE_KEY` (docs/updates.md) before images go to other people.
