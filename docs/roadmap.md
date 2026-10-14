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
| 6 | Push notifications (FCM) | hub | hub side done (events, offline, problems, aurora, summary, clear night); off until a Firebase service account is configured; the app side needs the Firebase project (Benjamin). Until then the same notices go by e-mail (#15) |
| 7 | ~~Signed agent updates with rollback (M5 part 2)~~ done | agent | port of myboxi's updater; needs the signing keys (docs/updates.md) before cameras go out |
| 8 | ~~Hot pixels without darks, sky mask, plate-solve orientation, event azimuths, NLC, aurora pole sector~~ (all done) | agent | |
| 9 | ~~Public sky page per camera~~ | hub | done: opt-in, random link, web + app API |
| 10 | ~~Self-service account: change password, delete account~~ | hub | done: web UI, app API and app |
| 11 | ~~Hub backups: database dump~~ | hub | daily dump done; the external rsync of /var/lib/allskyhub-server is Benjamin's |
| 12 | ~~Hub monitoring: health check, disk space, certificate, backup age~~ | hub | done (ntfy topic from Benjamin); camera offline is push (#6) |
| 13 | ~~Web UI: gallery and night products like in the app~~ | hub | done |
| 14 | ~~App: "Standort ändern" and camera settings via `set_settings`~~ | hub | done |
| 15 | ~~E-mail from the hub~~ done | hub | Mailcow on postkasten.cloud as no-reply@allskyhub.org: invitations, password reset, offline after 6 h, and on request all notices but single detections (problems, aurora, morning summary, clear night ahead, telescope unsafe) |
| 16 | ~~Public sky page like astronomy.garden, manageable on the hub and on the camera~~ done | hub + agent | sections, profile, `GET/PUT /device/v1/public`, loop, nights, events, sky charts, aurora, Kp, map, widget, Atom feed, lightbox, constellation overlay (H1), the camera's local editor (L10) |
| 17 | ~~Own domain for a public page~~ done | hub | CNAME check, Let's Encrypt via certbot standalone (nginx stopped for seconds, Benjamin agreed), root helper `deploy/domains/` |

## Local web portal (agent)

Benjamin, 2026-10-12: today the local UI (SPEC §7) shows only the live image and the
focus helper. It should do on the camera what the Allsky WebUI does, adapted to
allskyhub: everything works offline on the local network, and anything that is also in
the hub stays in step with it. The local work comes first; the Flutter app follows after
it. Order, except that L7 (camera settings) comes right after L1 because Benjamin misses
them most:

| # | Item | Owner | Notes |
|---|---|---|---|
| L1 | ✅ Local login: hub SSO + offline code | agent + hub | spec PR by the hub session; until then lighttpd basic auth on the test Pi. Done: #84, #85 (SSO via the hub and offline code); the test Pi's basic auth is gone |
| L2 | ✅ `packages/design` + layout with navigation (Live, Archive, Events, Sky, Settings, System), mobile-first, German/English | agent + hub | tokens from #76; red night mode so the screen does not ruin dark adaptation. Done: #113 |
| L3 | ✅ Live: image with zoom/fullscreen, frame data (time, exposure, gain, sensor °C, sun elevation, day/night), loop of the last hour | agent | Allsky "Live View". Done: #103, #113 |
| L4 | ✅ Archive: list of nights, image grid with thumbnails, lightbox, download; keogram, startrails, timelapse player per night | agent | Allsky "Images"; data in `ImageStore`, products from `products/build.py`. Done: #103, #113 |
| L5 | ✅ Events: meteors, aurora, lightning, NLC with image, time, azimuth; filter by type and night | agent | Allsky "Meteors" and the own modules; data from `detect/events.py`. Done: #103, #113 |
| L6 | ✅ Sky: charts per night for sky quality (SQM/Bortle), cloud cover, exposure/gain, sensor temperature | agent | Allsky "Charts"; `status.sky` history. Done: #103, #113 |
| L7 | ✅ Camera settings: location (map), time zone, day/night delay, exposure and gain limits, white balance, flip/rotation, JPEG quality | agent | Allsky "Allsky Settings" reduced to what the agent uses; extends `AgentSettings`, same validation as `set_settings`, the hub sees changes in `status`. Done: #113 and the portal round 2 (labels, targets, day capture, flip/rotate) |
| L8 | ✅ Detection and image setup: detectors on/off and sensitivity, sky-mask editor, orientation check (north, plate-solve result) | agent | replaces Allsky's module manager; no free-form modules. Done: detector levels in the settings schema (hub shows them too), drawn exclude/include areas, horizon and compass from the plate solve over the live image |
| L9 | ✅ Night products: which ones, timelapse fps/quality, retention | agent | settings `product_keogram/startrails/timelapse`, `timelapse_fps`, `timelapse_quality` (CRF 28/23/19), `keep_days`; the import takes over Allsky's `daystokeep` |
| L10 | ✅ Public page management (`GET/PUT /device/v1/public`) | agent + hub | read-only (last known state) when the hub is unreachable |
| L11 | ✅ Overlay: on/off and fields (time, exposure, temperature, stars/constellations) | agent | simple version of the Allsky overlay editor; Benjamin, 2026-10-12: drawn over the image when shown, never burned into the stored frames. Done: time, exposure/gain/sensor, sun, cardinal points over the live image, per-browser switches, download with overlay. Constellations from the shared `packages/sky`, the same drawing as the hub |
| L12 | ✅ System: CPU temperature/load, RAM, disk, uptime, camera, hub connection; restart agent, reboot, shut down | agent | Allsky "System"; reboot/power-off via logind, allowed to the agent's user by `image/files/etc/polkit-1/rules.d/51-allskyhub-power.rules`; Pi power warnings from `vcgencmd get_throttled` |
| L13 | ✅ Network: Wi-Fi status and change, LAN info | agent | setup-mode code exists; off with `--network none`; status read-only everywhere (`ip -j`, `nmcli`), switching only where the agent manages the network, SPEC §7.1 |
| L14 | ✅ Updates: version, channel, check/install, rollback | agent | updater from #7; requests `now`/`rollback` through `allskyhub-updater.path` (SPEC §8); the channel stays the image's manifest URL |
| L15 | ✅ Logs and support: agent journal, download a support bundle; backup/restore of settings | agent | Allsky "Support"; the agent keeps its last 2000 log lines itself (no journal rights), support ZIP without keys, sessions or images, backup = settings + drawn mask areas |
| L16 | ✅ N.I.N.A. integration: an ASCOM Alpaca server on the camera (discovery on UDP 32227, SafetyMonitor/0 with IsSafe from cloud cover, SQM, rain/lightning, stale data = unsafe; ObservingConditions/0 with cloudcover, skyquality), so NINA, SGP and Voyager connect without a plugin and "Loop while safe" works; optionally read NINA's Advanced API (host) and relay its state to the hub (C15) | agent | Benjamin, 2026-10-12; see Touch-N-Stars (GPL-3.0) and bortle.eu, which already reads NINA; needs a SPEC section. Done: Alpaca server (SafetyMonitor/ObservingConditions, discovery IPv4 + IPv6), settings `alpaca_*`, `safe_*`, `nina_url`, N.I.N.A. relay to `status.observatory`. ConformU 4.5.0 passes both devices (conformance and Alpaca protocol, 2026-10-13). No `nina_api_key`: the Advanced API has no authentication (Benjamin, 2026-10-13) |
| L17 | ✅ Watchdog on the camera: systemd watchdog for the agent, the Pi's hardware watchdog, reset the USB camera when no frames come, reboot when the local network (gateway, DNS) has been unreachable for hours, never just because the hub is down | agent | Benjamin, 2026-10-12; capture never depends on the hub (rule 1), so reboots only for local faults; SPEC §8.1: heartbeat (`WatchdogSec`), root check every 5 min (restart → USB reset → reboot, network 3 h), hardware watchdog 15 s |
| L18 | ✅ Import an existing Allsky installation: settings (location, camera, lens, owner, place → settings and public profile), all nights from `images/<date>/` with frame metadata from Allsky's database (`allsky_image`) or else from the file name, keogram, star trails and timelapse as night products, and the detections of Benjamin's own modules (meteors, aurora, lightning, NLC) as events. Hard links on the same disk (no copy, 35 GB on the test Pi), a dry run that shows what would come over, resumable. How the hub learns about old nights (today only the newest night's products are announced, SPEC §6.7) needs a SPEC note. Later the same for indi-allsky | agent | Benjamin, 2026-10-12: switching must not lose years of pictures. Done: images, products, meteors, sky log, lightning (with storms), aurora and NLC episodes from Benjamin's modules (2026-10-13). indi-allsky (`import-indi-allsky`, 2026-10-13): location, JPEG images with metadata from its database, keograms, star trails and H.264 timelapses (night and day); not its Hough-line detections (no meteor/plane distinction), its uncalibrated jSQM, PNG/TIFF images or WebM timelapses |
| L19 | ✅ Bad frames: mark frames that are too dark or too bright and leave them out of products, loop and grids | agent | Benjamin, 2026-10-13, from the feature comparison (Allsky "remove bad images"): mark, never delete; SPEC §4.9. Blur and dew stay with C12. Done: `FrameInfo.bad`, settings `bad_frames`, `bad_dark_pct`, `bad_bright_pct`; the hub hides them too |
| L20 | ✅ Moon mode: shorter night exposure and lower gain while a bright moon is up, so the sky does not burn out | agent | Benjamin, 2026-10-13 (indi-allsky "moon mode"): a lower target brightness, no own limits; SPEC §4.3; moon from `packages/sky`. Done: setting `moon_mode` (default on) |
| L21 | ✅ More products: day timelapse, a short "mini" timelapse of the last hour for the live page, keolapse (keogram over many nights) | agent | Benjamin, 2026-10-13: day timelapse and keolapse on, day keogram and mini timelapse off by default; SPEC §5.2, §5.4. Done: `product_day_timelapse`, `product_day_keogram`, `product_mini_timelapse`, keolapse in the archive; day products go to the hub once a hub that skips unknown product kinds (#163) runs |
| L22 | Cloud cover by day: estimate cloud cover from daytime frames (sky colour) so the charts and the hub have it around the clock | agent | Benjamin, 2026-10-13; today cloud cover comes from star counts and is night-only; SPEC §6.3. Blue/red fails without an IR cut filter: the method is tested on real day frames first (Benjamin, 2026-10-13) |
| L23 | ✅ MQTT: publish status, sky values and detections to a local broker (Home Assistant discovery) | agent | Benjamin, 2026-10-13 (indi-allsky, Allsky module); off by default, settings for broker, user, password (`write_only`), topic prefix; SPEC §7.7. Done: own MQTT 3.1.1 publisher, no new dependency; the first `write_only` setting (`mqtt_password`) |
| L24 | ✅ HTTPS for the local portal: a self-signed certificate made on first boot, optionally the user's own | agent | Benjamin, 2026-10-13 (indi-allsky); off by default (Benjamin, 2026-10-13); SPEC §7.6. Done: setting `https`, port 8443, own certificate upload on the system page |
| L25 | ✅ Trail verdicts: keep every streak, say from the trail why it is not a meteor (moving, great circle, blinking, fragments, star, star trail, glare, edge glow, repeat) and label it automatically | agent | Benjamin, 2026-10-13: like his Allsky modules Meteor Detection and Sky Traffic; mark, never delete; all checks label (no shadow mode); SPEC §6.4 Trail verdicts. Done: #207 (agent), #208 (hub) |

Not ported on purpose: the free-form module manager and file editor (fixed detectors
instead), the website upload/remote server (the hub and its public page do that).

## Cloud features (hub)

Benjamin, 2026-10-12: all of them, built one after another once the local login (L1) has its
hub side. Push needs the Firebase project; until then the same messages show on the web.

| # | Item | Owner | Notes |
|---|---|---|---|
| C1 | ~~"Heute Nacht": observing window from a cloud forecast (Open-Meteo), astronomical twilight and the moon (phase, rise, set), on the camera page, public page and app; evening push~~ done | hub | own sun/moon code (Meeus, low precision), no new dependency |
| C2 | ~~Aurora alarm that looks: push when Kp reaches the camera's latitude threshold **and** the camera reports a clear sky~~ done | hub | Kp from #16, cloud cover from `status.sky` |
| C3 | ~~Morning summary per camera: meteors, clear hours, best sky brightness, the night's best picture; push, later e-mail (#15)~~ done | hub | |
| C4 | ~~Meteor triangulation: the same meteor seen by two oriented cameras gives its path, height and radiant~~ done | hub | needs `azimuth_deg`/`altitude_deg` of both ends from the agent (SPEC change) |
| C5 | ~~Map of all public cameras~~ done | hub | coordinates rounded as on the public page, only where the owner shows them |
| C6 | ~~Satellite passes (ISS, bright satellites) as a forecast~~ done; naming satellite trails among the meteors is H6 | hub | TLE from CelesTrak, SGP4; the pass forecast only covers ISS, Tiangong and Hubble |
| C7 | ~~Statistics per month and year: clear nights, sky brightness, meteors per shower~~ done | hub | from `sky_sample` and `event` |
| C8 | ~~Timelapse over a chosen period and monthly keograms from the archive~~ done | hub | thumbnails only, ffmpeg on the hub |
| C9 | ~~Embeddable widget and image feed for own websites, RSS/Atom for detections~~ done | hub | per public page |
| C10 | ~~Share a camera with family or a club (roles: viewer, admin)~~ done | hub | |
| C11 | ~~Export for science: meteors for IMO/GMN, prefilled fireball reports, sky brightness for Globe at Night~~ done | hub | |
| C12 | ~~Maintenance hints: dew or dirt on the dome, recognised from the pictures~~ done | hub | |
| C13 | ~~Aurora map (NOAA OVATION) on the public page~~ done | hub | |
| C14 | ~~Camera monitoring with escalation: push after 30 min offline (exists), e-mail after a few hours (#15); "online but no new pictures", disk almost full, sensor or CPU too hot, clock not synchronised, camera missing; daily health line on the camera page~~ done | hub | Benjamin: what other projects do not do; the camera's own watchdog is L17 |
| C15 | ~~N.I.N.A. on the hub: show what the telescope is imaging (target, sequence progress, last image) next to the sky, push when the sky turns unsafe~~ done (hub side; the data comes with L16) | hub | data relayed by the agent (L16); opt-in, NINA is LAN-only |

## Hub, also done (October 2026)

- Measurement viewer: scientific charts (uPlot) with twilight bands, sun and moon, log
  exposure, linked zoom, CSV/PNG export, nights of a year; on the camera, night and
  public pages.
- Lightbox for all photos (zoom, pinch, swipe through a night).
- Admin page `/admin`: running version, new commits and their CI, deploy by button
  (root helper `deploy/admin/`), mail test, disk.
- `/healthz` reports the running commit.

## Hub, next

| # | Item | Owner | Notes |
|---|---|---|---|
| H1 | ~~Constellation lines, names and N/E/S/W over the live image on the public page~~ done | hub (+ agent: lens model in status) | lens in `status.orientation` (SPEC §4.8, §6.3), #135; shared drawing in `packages/sky` (#142) |
| H2 | ~~Flutter app catches up with the web: charts, lightbox, sharing, statistics, "Heute Nacht", exports, e-mail notification switches~~ done | hub | done 2026-10 (in parallel with the local work) |
| H3 | Aircraft from ADS-B: label planes in the live image and drop their trails from meteor detections | hub + agent | Benjamin, 2026-10-13 (indi-allsky ADS-B): adsb.lol on the hub plus, optionally, the owner's own receiver on the camera (`adsb_url`); a match is an automatic label false_positive/aircraft, never a deletion; SPEC §7.8 |
| H4 | "Ziel frei?": is a chosen object (or a sky area) clear now and in the next hours, from the camera's cloud map and the forecast; light-pollution map next to it | hub | Benjamin, 2026-10-13; from the roadmap brainstorm 2026-09-26. Done: planets and Messier, height, darkness, moon and forecast per hour, the camera's overall cloud cover now (web and app); light-pollution view on the camera map from the cameras' own SQM (Benjamin, 2026-10-13). Also done: the cell of the camera's cloud map (SPEC §6.3 `cloud_map`) in the object's direction |
| H5 | Upload the night's timelapse to YouTube and post detections to Discord | hub | Benjamin, 2026-10-13 (indi-allsky/Allsky); the owner connects the accounts on the hub, never on the camera. Done: Discord (webhook per camera, web and app). Open: YouTube (needs a Google Cloud project with YouTube API access from Benjamin) |
| H6 | Satellites among the meteors: name them from CelesTrak (SGP4, sunlit), ISS and Tiangong by name, automatic label | hub | Benjamin, 2026-10-13: on the hub, not on the camera; SPEC §7.9 |

## Network features (hub)

Benjamin, 2026-10-14: "Mach alles", Firebase last. What a single camera cannot do.

| # | Item | Owner | Notes |
|---|---|---|---|
| N1 | Fireball alarm with a fall area | hub | done: a triangulated meteor ending below 35 km gives a rough fall strip (no speed, no wind), an alarm once per meteor to the cameras' owners and network members within 300 km (`notify_fireball`), public list `/feuerkugeln` when both cameras are in the network |
| N2 | Aurora network: a real aurora event from a camera further north alerts clear-sky users | hub | |
| N3 | Live meteor showers: radiant and hourly rate from all cameras, public page | hub | |
| N4 | Cloud nowcast from neighbouring cameras' `cloud_map` and the wind | hub | |
| N5 | Light-pollution trend over the years from the cameras' SQM | hub | |
| N6 | App: home-screen widget, AR sky view | hub | |
| N7 | Paid tier | hub | needs Benjamin's decisions (provider, prices) |

## Security (review 2026-10-13)

Read-only reviews of hub and camera by the hub session; the camera points were checked
again in the code. Benjamin decided the open questions on 2026-10-13.

| # | Item | Owner | Notes |
|---|---|---|---|
| S1 | ✅ Local UI: refuse cross-site POSTs, no framing; Host allow-list against DNS rebinding with the setting `local_hostnames` | agent | Origin check, X-Frame-Options: #204; Host list: SPEC §7.4. Done: #204, #210 |
| S2 | ✅ Setup network with WPA2 and a per-camera setup key (QR label, file on the boot partition), `hub_url` https only | agent + hub (app) | SPEC §7.1; the app scans the Wi-Fi QR code. Done: #214 (agent, image), #212 (app) |
| S3 | ✅ MQTT password deleted when the broker changes; MQTT, N.I.N.A. and ADS-B addresses owner-only on the hub | agent + hub | SPEC §7.7; hub part in #202. Done: #210 (agent), #202 (hub) |
| S4 | ✅ Release pipeline before update keys exist: protected environment with a reviewer, tag must be on `main`, actions pinned by SHA; key revocation | agent | #206; revocation with `keys.json`, signed by the emergency key (SPEC §8 Key revocation) |
| S5 | ✅ Root tools never write through links in the agent's folder | agent | #204 |
| S6 | ✅ Offline code: tries counted before Argon2, one at a time, parameters capped | agent | #204; caps in SPEC §6.5 |
| S7 | ✅ Image: nftables input filter, narrower polkit rule for NetworkManager, reboot by day for security updates | agent | SPEC §8; reboot: #215 |
| S8 | ✅ Wi-Fi credentials without control characters | agent | #204 |

Leftovers of the hub audit (2026-10-13), done 2026-10-14 after Benjamin's "Mach alles":

- L2: meteors of other owners' cameras are triangulated only when both owners joined the
  network (`Device.network`, switch "Kameranetz" on the camera page and in the app API);
  showing rounded coordinates on a public page no longer counts as consent.
- L5: login attempts count per address and IP (10 per 15 min), plus a cap per address
  across all IPs (100 per 15 min, 500 per day).
- L6: the local-login confirm page shows the target host large and warns when it does not
  look like a home network or VPN address; other addresses stay allowed (SPEC §7.4 A).
- HSTS with `includeSubDomains` on allskyhub.org (all its names point to this server and
  answer over HTTPS); own domains keep plain HSTS, their subdomains are not ours.
- `/healthz` keeps the commit: the admin deploy checks it, and the code is public anyway.

Leftovers of the hub audit (2026-10-13), done 2026-10-14 after Benjamin's "Mach alles":

- L2: meteors of other owners' cameras are triangulated only when both owners joined the
  network (`Device.network`, switch "Kameranetz" on the camera page and in the app API);
  showing rounded coordinates on a public page no longer counts as consent.
- L5: login attempts count per address and IP (10 per 15 min), plus a cap per address
  across all IPs (100 per 15 min, 500 per day).
- L6: the local-login confirm page shows the target host large and warns when it does not
  look like a home network or VPN address; other addresses stay allowed (SPEC §7.4 A).
- HSTS with `includeSubDomains` on allskyhub.org (all its names point to this server and
  answer over HTTPS); own domains keep plain HSTS, their subdomains are not ours.
- `/healthz` keeps the commit: the admin deploy checks it, and the code is public anyway.

## Needs Benjamin / hardware

- First boot of the image on the spare Pi 4 (SD card ordered); test with `"camera": "sim"`.
- Raspberry Pi HQ camera (CS mount) to test the rpi-hq profile; fits the Altair 1.55 mm.
- Apple developer account (open), Play Console via WebInx.
- Housing: Daniel Nimmervoll's design.
- ~~Update signing keys~~ done 2026-10-13: both public keys in the image, the main key as
  secret of the reviewed environment `release` (docs/updates.md); revocation with the key list.
