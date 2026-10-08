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
| 1 | Meteor detection → `event` (meteor) with image | agent | port of allsky_meteordetect; hub/app show events (hub) |
| 2 | `set_settings` command: location, time zone, camera, delays | agent + hub | app "Standort ändern" |
| 3 | Free-space retention (keep ≥ 10 % free) | agent | SPEC §4.5 says "from M1" |
| 4 | Wi-Fi country from the app | agent | root helper unit started via polkit |
| 5 | Lightning, aurora, NLC, sky quality, cloud detections | agent | ports of the existing Allsky modules |
| 6 | Push notifications (FCM) for events | hub | Android first |
| 7 | Signed agent updates with rollback (M5 part 2) | agent | like myboxi's updater |
| 8 | Dark frames, sky mask, plate-solve alignment | agent | |
| 9 | Public sky page per camera | hub | opt-in |

## Needs Benjamin / hardware

- First boot of the image on the spare Pi 4 (SD card ordered); test with `"camera": "sim"`.
- Raspberry Pi HQ camera (CS mount) to test the rpi-hq profile; fits the Altair 1.55 mm.
- Apple developer account (open), Play Console via WebInx.
- Housing: Daniel Nimmervoll's design.
