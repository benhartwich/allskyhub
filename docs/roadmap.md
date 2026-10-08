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
| 5 | Lightning (done), aurora, NLC, sky quality, cloud detections | agent | ports of the existing Allsky modules; lightning v1 detects only, a short "storm exposure" comes later; cloud cover and SQM go into `status` as `sky` |
| 6 | Push notifications (FCM) for events | hub | hub side done (off until a Firebase service account is configured); app side needs the Firebase project (Benjamin) |
| 7 | Signed agent updates with rollback (M5 part 2) | agent | like myboxi's updater |
| 8 | Dark frames, sky mask, plate-solve alignment | agent | |
| 9 | ~~Public sky page per camera~~ | hub | done: opt-in, random link, web + app API |
| 10 | ~~Self-service account: change password, delete account~~ | hub | done: web UI, app API and app |
| 11 | Hub backups: database dump and image archive, restore tested | hub | |
| 12 | Hub monitoring: health check, disk space alert, camera offline notice | hub | camera offline also as push (#6) |
| 13 | ~~Web UI: gallery and night products like in the app~~ | hub | done |
| 14 | ~~App: "Standort ändern" and camera settings via `set_settings`~~ | hub | done |

## Needs Benjamin / hardware

- First boot of the image on the spare Pi 4 (SD card ordered); test with `"camera": "sim"`.
- Raspberry Pi HQ camera (CS mount) to test the rpi-hq profile; fits the Altair 1.55 mm.
- Apple developer account (open), Play Console via WebInx.
- Housing: Daniel Nimmervoll's design.
