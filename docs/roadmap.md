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
it. Order, except that L7 (camera settings) comes right after L1 because Benjamin misses
them most:

| # | Item | Owner | Notes |
|---|---|---|---|
| L1 | Local login: hub SSO + offline code | agent + hub | spec PR by the hub session; until then lighttpd basic auth on the test Pi |
| L2 | `packages/design` + layout with navigation (Live, Archive, Events, Sky, Settings, System), mobile-first, German/English | agent + hub | tokens from #76; red night mode so the screen does not ruin dark adaptation |
| L3 | Live: image with zoom/fullscreen, frame data (time, exposure, gain, sensor °C, sun elevation, day/night), loop of the last hour | agent | Allsky "Live View" |
| L4 | Archive: list of nights, image grid with thumbnails, lightbox, download; keogram, startrails, timelapse player per night | agent | Allsky "Images"; data in `ImageStore`, products from `products/build.py` |
| L5 | Events: meteors, aurora, lightning, NLC with image, time, azimuth; filter by type and night | agent | Allsky "Meteors" and the own modules; data from `detect/events.py` |
| L6 | Sky: charts per night for sky quality (SQM/Bortle), cloud cover, exposure/gain, sensor temperature | agent | Allsky "Charts"; `status.sky` history |
| L7 | Camera settings: location (map), time zone, day/night delay, exposure and gain limits, white balance, flip/rotation, JPEG quality | agent | Allsky "Allsky Settings" reduced to what the agent uses; extends `AgentSettings`, same validation as `set_settings`, the hub sees changes in `status` |
| L8 | ✅ Detection and image setup: detectors on/off and sensitivity, sky-mask editor, orientation check (north, plate-solve result) | agent | replaces Allsky's module manager; no free-form modules. Done: detector levels in the settings schema (hub shows them too), drawn exclude/include areas, horizon and compass from the plate solve over the live image |
| L9 | ✅ Night products: which ones, timelapse fps/quality, retention | agent | settings `product_keogram/startrails/timelapse`, `timelapse_fps`, `timelapse_quality` (CRF 28/23/19), `keep_days`; the import takes over Allsky's `daystokeep` |
| L10 | Public page management (`GET/PUT /device/v1/public`) | agent + hub | spec PR by the hub session; read-only when the hub is unreachable |
| L11 | Overlay: on/off and fields (time, exposure, temperature, stars/constellations) | agent | simple version of the Allsky overlay editor |
| L12 | System: CPU temperature/load, RAM, disk, uptime, camera, hub connection; restart agent, reboot, shut down | agent | Allsky "System"; needs a narrow root path (polkit) like the Wi-Fi country |
| L13 | Network: Wi-Fi status and change, LAN info | agent | setup-mode code exists; off with `--network none` |
| L14 | Updates: version, channel, check/install, rollback | agent | updater from #7 |
| L15 | Logs and support: agent journal, download a support bundle; backup/restore of settings | agent | Allsky "Support" |
| L16 | N.I.N.A. integration: an ASCOM Alpaca server on the camera (discovery on UDP 32227, SafetyMonitor/0 with IsSafe from cloud cover, SQM, rain/lightning, stale data = unsafe; ObservingConditions/0 with cloudcover, skyquality), so NINA, SGP and Voyager connect without a plugin and "Loop while safe" works; optionally read NINA's Advanced API (host + key) and relay its state to the hub (C15) | agent | Benjamin, 2026-10-12; see Touch-N-Stars (GPL-3.0) and bortle.eu, which already reads NINA; needs a SPEC section |
| L17 | Watchdog on the camera: systemd watchdog for the agent, the Pi's hardware watchdog, reset the USB camera when no frames come, reboot when the local network (gateway, DNS) has been unreachable for hours, never just because the hub is down | agent | Benjamin, 2026-10-12; capture never depends on the hub (rule 1), so reboots only for local faults |
| L18 | Import an existing Allsky installation: settings (location, camera, lens, owner, place → settings and public profile), all nights from `images/<date>/` with frame metadata from Allsky's database (`allsky_image`) or else from the file name, keogram, star trails and timelapse as night products, and the detections of Benjamin's own modules (meteors, aurora, lightning, NLC) as events. Hard links on the same disk (no copy, 35 GB on the test Pi), a dry run that shows what would come over, resumable. How the hub learns about old nights (today only the newest night's products are announced, SPEC §6.7) needs a SPEC note. Later the same for indi-allsky | agent | Benjamin, 2026-10-12: switching must not lose years of pictures |

Not ported on purpose: the free-form module manager and file editor (fixed detectors
instead), the website upload/remote server (the hub and its public page do that).

## Cloud features (hub)

Benjamin, 2026-10-12: all of them, built one after another once the local login (L1) has its
hub side. Push needs the Firebase project; until then the same messages show on the web.

| # | Item | Owner | Notes |
|---|---|---|---|
| C1 | "Heute Nacht": observing window from a cloud forecast (Open-Meteo), astronomical twilight and the moon (phase, rise, set), on the camera page, public page and app; evening push | hub | own sun/moon code (Meeus, low precision), no new dependency |
| C2 | Aurora alarm that looks: push when Kp reaches the camera's latitude threshold **and** the camera reports a clear sky | hub | Kp from #16, cloud cover from `status.sky` |
| C3 | Morning summary per camera: meteors, clear hours, best sky brightness, the night's best picture; push, later e-mail (#15) | hub | |
| C4 | Meteor triangulation: the same meteor seen by two oriented cameras gives its path, height and radiant | hub | needs `azimuth_deg`/`altitude_deg` of both ends from the agent (SPEC change) |
| C5 | Map of all public cameras | hub | coordinates rounded as on the public page, only where the owner shows them |
| C6 | Satellite passes (ISS, bright satellites) as a forecast and to drop satellite trails from meteor detections | hub | TLE from CelesTrak, SGP4 |
| C7 | Statistics per month and year: clear nights, sky brightness, meteors per shower | hub | from `sky_sample` and `event` |
| C8 | Timelapse over a chosen period and monthly keograms from the archive | hub | thumbnails only, ffmpeg on the hub |
| C9 | Embeddable widget and image feed for own websites, RSS/Atom for detections | hub | per public page |
| C10 | Share a camera with family or a club (roles: viewer, admin) | hub | |
| C11 | Export for science: meteors for IMO/GMN, prefilled fireball reports, sky brightness for Globe at Night | hub | |
| C12 | Maintenance hints: dew or dirt on the dome, recognised from the pictures | hub | |
| C13 | Aurora map (NOAA OVATION) on the public page | hub | |
| C14 | Camera monitoring with escalation: push after 30 min offline (exists), e-mail after a few hours (#15); "online but no new pictures", disk almost full, sensor or CPU too hot, clock not synchronised, camera missing; daily health line on the camera page | hub | Benjamin: what other projects do not do; the camera's own watchdog is L17 |
| C15 | N.I.N.A. on the hub: show what the telescope is imaging (target, sequence progress, last image) next to the sky, push when the sky turns unsafe | hub | data relayed by the agent (L16); opt-in, NINA is LAN-only |

## Needs Benjamin / hardware

- First boot of the image on the spare Pi 4 (SD card ordered); test with `"camera": "sim"`.
- Raspberry Pi HQ camera (CS mount) to test the rpi-hq profile; fits the Altair 1.55 mm.
- Apple developer account (open), Play Console via WebInx.
- Housing: Daniel Nimmervoll's design.
- Update signing keys: create the main and emergency key pair, commit the public keys, set
  the secret `ALLSKYHUB_UPDATE_KEY` (docs/updates.md) before images go to other people.
