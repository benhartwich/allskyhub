# allskyhub – Specification (draft v0.2)

This document is the contract for the data model, the device protocol and the camera
behaviour. Code references its sections (`# SPEC §4.3`). When code and spec disagree,
the spec is changed first.

## 1. Goals

allskyhub is a plug-and-play all-sky camera: a Raspberry Pi with a known camera, a
preinstalled image, a phone app and an optional hosted hub. It is built around the
problems people actually report with existing all-sky software (see
`docs/research.md`):

1. **Zero-touch setup.** Flash or buy, power on, pair with the phone. No SSH, no
   installer, no dependency builds.
2. **Curated hardware.** A short list of tested camera/lens/Pi combinations, each with a
   profile (§3). Anything else is "community supported".
3. **Outbound only.** The camera connects out to the hub. No port forwarding, no FTP, no
   web hosting.
4. **Capture never depends on the network.** The camera keeps capturing, storing and
   processing when the hub is unreachable and catches up later.
5. **Safe updates.** Signed update packages with automatic rollback (§8).
6. **Good images out of the box.** Auto exposure, day/night switching, darks, mask and
   focus help work without tuning (§4).
7. **Mobile first.** Every routine task can be done from the phone.

Non-goals for v1: arbitrary cameras, Windows/macOS capture, self-built overlay editors.

## 2. Architecture

```
 camera (Pi)                      hub (server)                 phone
 ┌────────────────────┐  HTTPS/   ┌──────────────────┐  HTTPS  ┌──────────┐
 │ allskyhub-agent    │──WSS────▶ │ device API       │ ◀────── │ app      │
 │  capture loop      │  (out)    │ web + app API    │         │ (Flutter)│
 │  processing        │           │ public sky pages │         └──────────┘
 │  local web UI      │           │ push alerts      │
 └────────────────────┘           └──────────────────┘
```

- **Agent** (`agent/`, GPL-3.0-or-later): runs on the Pi, owns the camera.
- **Protocol** (`packages/protocol/`, Apache-2.0): message models shared by agent, hub
  and third parties.
- **Hub** (`server/`, AGPL-3.0-or-later): pairing, storage, public pages, alerts. The
  project's hosted hub runs at `allskyhub.org`; anyone can run their own.
- **App** (`app/`, Apache-2.0): Flutter, built for Android and iOS with Codemagic.

## 3. Hardware profiles

A profile describes one tested combination: Pi model, camera driver and model, sensor
size and pixel size, lens (focal length, f-number, projection), exposure and gain
limits, and the default image circle. Profiles are data files shipped with the agent.

Initial targets (to be confirmed on real hardware):

| Profile | Pi | Camera | Lens |
|---|---|---|---|
| `zwo-asi678mc` | Pi 4 / Pi 5 | ZWO ASI678MC (IMX678) | 1.55–2.5 mm fisheye |
| `rpi-hq` | Pi 4 / Pi 5 | Raspberry Pi HQ (IMX477) | 1.55 mm fisheye |

## 4. Capture

### 4.1 Loop

The agent captures continuously. For every frame it decides the mode (§4.2), asks the
exposure controller (§4.3) for exposure and gain, captures, stores (§4.5) and passes
the frame to processing. The pause between frames is configurable per mode (default:
day 30 s, night 0 s).

By day the setting `day_capture` decides (Benjamin, 2026-10-13): `save` (default) stores
day frames like night frames; `live` takes them for the live image and the hub's latest
image but does not store them (no day products, no day frames in the archive); `off`
takes none and only checks the sun every minute, so the night starts on time.

**Pausing.** The owner can pause capture in the local UI (e.g. to clean the dome) and
resume it; the pause survives restarts (`<data>/capture-paused`) until resumed. While
paused the agent takes no frames, reports `capture_paused: true` in `status` (§6.3), and
neither the heartbeat nor the watchdog (§8.1) counts it as a stall; the hub's monitoring
(roadmap C14) does not warn about missing pictures.

**Flip and rotation.** `image_flip` (`none` default, `horizontal`, `vertical`) and
`image_rotate` (0 default, 90, 180, 270, clockwise) turn every frame right after capture,
before anything else sees it, so stored frames, products, detections and the live image
all show the turned picture. Hot pixels, the sky mask and the orientation are pixel maps
of the old picture: a change of either setting discards them and they are learned again
(§4.6–§4.8).

### 4.2 Day and night

The mode follows the sun's elevation at the camera's location:

- `night` when the elevation is below `night_angle` (default −6°),
- `day` when it is above `night_angle + hysteresis` (default hysteresis 1°),
- otherwise the previous mode is kept.

The sun position uses the NOAA approximation (accuracy better than 0.1° near the
horizon, more than enough here). Time comes from an injected clock.

### 4.3 Auto exposure

The controller aims for a target mean brightness (`target_mean`, 0..1, default day
0.35, night 0.20) measured inside the mask. The owner sets them as `day_target_pct` and
`night_target_pct` (5..80 %; Allsky's `daymean`/`nightmean`).

- It works in log space: `ratio = target / measured`, damped as
  `ratio ** damping` (default 0.7) and clamped to `[1/8, 8]` per frame, so a single odd
  frame cannot swing the exposure wildly.
- Exposure is changed first. Gain is raised only when the exposure is at its mode's
  maximum, and lowered first when the image is too bright.
- A measured mean of 0 is treated as `1/65536` so the controller can always recover.
- On a mode change the controller starts from that mode's last known good values.

**Moon mode** (roadmap L20, setting `moon_mode`, default on). At night, while the moon is
above the horizon and at least half lit, the night target is 0.6 times the night
target (0.12 for the default 0.20), so the moonlit sky and the landscape do
not burn out; as always, gain only rises once the exposure is at its maximum. The moon
position and phase come from `allskyhub_sky.astro` (low precision, Meeus). Switching in or
out of moon mode only moves the target; the damping above makes the change gradual.

### 4.4 Frame metadata

Every frame carries: capture time (UTC), mode, exposure (µs), gain, measured mean,
sun elevation, sensor temperature if available, the profile id, and `bad` (§4.9; null
for a good frame, missing from older agents).

Timestamps come from the system clock, synchronized by NTP. A Pi has no real-time clock,
so after a boot without network the clock can be wrong: the agent reports
`time_trusted` (true once the clock is NTP-synchronized) in every `status` (§6.3), and
the hub treats frames from a device with `time_trusted: false` as possibly misdated.

### 4.5 Storage

Frames are stored as JPEG under `<data>/images/<night-id>/` with a thumbnail. The
*night id* is the date of the evening the night started: frames before local noon
belong to the previous day's id, so a whole night lands in one folder. Retention is
by days (setting `keep_days`, §6.9, default 14, after the night products; an import of an
existing installation raises it to that installation's retention (the longest, if it keeps
images and products for different times) and to the age of its oldest night) and by free disk space: every 10 minutes the oldest nights are removed until at least 10 % of the disk is free; the current night is never removed.

### 4.6 Hot pixels

No dark frames: they need the lens covered. The agent finds hot pixels itself in the
minimum of the night's frames (sun ≤ −12°, one frame per 10 min, at least 12 frames over
2 h): stars and clouds have moved on, a hot pixel is a spot of at most 4 px that is much
brighter than its surroundings. A pixel is corrected only if it was found in two nights
in a row (the first night's map alone until then). The map is rebuilt every night, kept
in `<data>/calibration/hotpixels.npz`, built from raw frames, and every frame is stored
with the mapped pixels and their 3×3 neighbourhood replaced by the local median.

### 4.7 Sky mask

The agent learns which part of the frame is sky: in the median of a night's frames (sun
≤ −12°, one per 10 min, at least 12 over 2 h) the glowing sky is brighter than trees,
roofs and the corners outside the lens; Otsu's threshold and the largest bright region,
with holes filled, give the mask. A mask covering less than 20 % or more than 97 % of
the frame is not used. It is relearned every night, kept in
`<data>/calibration/skymask.png`, and used for metering (§4.3), the detections and the
sky values in `status` (§6.3); until there is one, the profile's image circle is used.

The owner can draw areas in the local UI (roadmap L8), kept in
`<data>/calibration/skymask_edits.json` as `{exclude, include}`, each a list of polygons
of 3–200 `[x, y]` points in fractions of the frame (at most 30 polygons). `exclude` is never
sky, `include` always is; they apply on top of the learned mask (or, until there is one,
the image circle) from the next frame on and survive relearning.

Each detector has a level in the settings (§6.9): `detect_meteor`, `detect_aurora`,
`detect_lightning`, `detect_nlc`, `detect_transient` (roadmap L27), one of `off`, `low`, `normal` (default), `high`. `off`
runs no detection of that kind; the other levels move only the threshold that separates
faint events from false alarms.

### 4.8 Orientation

The agent plate-solves a clear frame (sun ≤ −18°, sky meter ≤ 10 % cloud and ≥ 150
stars, with a second clear frame 20–40 min earlier whose points mark text and hot
pixels) against the stars brighter than magnitude 3 (Hipparcos) at the frame's
mid-exposure time and the camera's location; the clock must be trusted (§4.4). Two
point pairs give guesses for centre, scale and rotation; the best are refined with the
lens r = a1·t + a3·t³ (t = zenith angle / 90°), a small tilt and a shrinking search
window, and only a clear winner (≥ 12 stars, ≤ 0.6° RMS, no close rival) is accepted.
At most 3 tries a night, solved again after 7 days; the result is kept in
`<data>/calibration/orientation.json` and reported in `status` (§6.3).

The fitted lens maps a direction (alt, az) to a pixel (x, y) of the solved frame:

1. Tilt: the camera axis leans `tilt_east_deg` toward east and `tilt_north_deg` toward
   north. With τ = √(tilt_east² + tilt_north²) and φ = atan2(tilt_east, tilt_north), the
   axis is a = (sin τ·sin φ, sin τ·cos φ, cos τ) in (east, north, up); the direction
   v = (cos alt·sin az, cos alt·cos az, sin alt) is rotated by τ about k = a × (0, 0, 1)
   / |a × (0, 0, 1)| (Rodrigues), giving alt′, az′. Without tilt alt′ = alt, az′ = az.
2. t = (90° − alt′) / 90°, r = a1·t + a3·t³.
3. ang = north_deg − az′ when `mirrored`, else north_deg + az′;
   x = cx + r·sin ang, y = cy − r·cos ang.

`cx`, `cy`, `a1`, `a3` are pixels of a `width` × `height` frame; for another size of the
same frame (thumbnails, the latest image) they scale with the width.

### 4.9 Bad frames

Roadmap L19. A frame whose measured mean (§4.3) is below `bad_dark_pct` (default 2 %) or
above `bad_bright_pct` (default 90 %) is a bad frame, typically while the exposure is
still catching up at dusk or dawn, or when a light is switched on next to the camera. It
is stored as usual and marked in its metadata: `bad` is `dark` or `bright`. It is never
deleted for this: detections run on it as on every other frame (a lightning flash makes
a frame bright), and retention (§4.5) removes it with its night. Bad frames are left out
of the night products (§5.2), the loop on the live page and the image grids of the local
UI and the hub; the local archive shows them on request. `bad_frames` (default on)
switches the marking off. Blur and dew are not judged per frame here: dew and dirt are
recognised over many frames on the hub (roadmap C12).

## 5. Products (M4)

### 5.1 Frame index

Every stored frame is appended to `<data>/images/<night-id>/frames.jsonl`, one JSON
object per line: the frame's metadata (§4.4, the `frame` message body). Products and
the hub's gallery read it instead of decoding every image. A missing or damaged line is
skipped. Next to it, `sky.jsonl` keeps the `sky` measurement (§6.3) of every frame that
has a cloud cover or sky brightness, for the local charts (§7).

### 5.2 Night products

At dawn, when the mode switches from night to day, the agent builds three products from
that night's frames (frames with mode `night` in the night id's index) in a background
worker; capture never waits for it.
Each can be switched off (`product_keogram`, `product_startrails`, `product_timelapse`,
§6.9); the timelapse takes `timelapse_fps` (default 25) and `timelapse_quality`
(`small`, `normal`, `high`).

- **Keogram** `keogram.jpg`: one column per frame, cut along the vertical line through
  the image centre (a 3-pixel-wide band, averaged), in capture order; height scaled to
  at most 1080 px.
- **Startrails** `startrails.jpg`: the per-pixel maximum of all frames whose mean
  brightness is at most `startrails_max_mean` (default 0.35), so twilight and moonlit
  or cloud-lit frames do not wash out the trails. Built only with at least 10 such frames.
- **Timelapse** `timelapse.mp4`: H.264, 25 fps, width at most 1920 px, `yuv420p`, built
  with `ffmpeg` from the night frames in capture order.

Each product gets a thumbnail in `thumbnails/` (same name, `.jpg`). A product that
cannot be built (too few frames, no `ffmpeg`) is skipped and logged; the others are
still built. Building again replaces the old files (written to a temp name, then
renamed). Products of a night are kept as long as its folder (§4.5).

**Day products** (roadmap L21). At dusk, when the mode switches from day to night, the
agent builds `day-timelapse.mp4` (`product_day_timelapse`, default on) and
`day-keogram.jpg` (`product_day_keogram`, default off) the same way from the day frames of
that local date: the morning's frames are in the previous night id's folder (before
noon, §4.5), the afternoon's in the current one; the files go into the current one. They
are listed in that night id's `products` message (§6.3) as `day_timelapse` and
`day_keogram`, sent again when they have been built; a hub that does not know these
kinds skips them (§6.1).

**Mini timelapse** (`product_mini_timelapse`, default off): every 10 minutes the agent
renders the last hour's good frames (at most 640 px wide, 25 fps) to `<data>/live/mini.mp4`
for the live page; it is replaced each time and never archived.

### 5.3 Detections

Meteor, lightning, aurora, noctilucent clouds, sky quality and cloud cover are emitted
as protocol events (§6.4).

### 5.4 Keolapse

The local archive (roadmap L21) shows the keograms of a range of nights side by side, each
night's `keogram.jpg` scaled to the same height (540 px) and a width of 48 px, oldest on
the left, at most 92 nights. It is built on request (`GET /api/keolapse.jpg?from=&to=`,
night ids) from existing keograms and cached in `<data>/cache/` until a night in the
range changes; nights without a keogram leave a grey gap.

## 6. Device protocol v1 (draft)

### 6.1 Transport

The agent opens an outbound WebSocket (`wss://<hub>/device/v1/ws`, default hub
`allskyhub.org`) and uses HTTPS for registration, tokens and uploads (§6.6). Every
WebSocket message is an envelope `{v: 1, type, id, ts, body}`.

**Compatible changes.** Within v1 either side may add optional fields to any message,
request or answer. Receivers ignore fields they do not know, so a hub can be newer than
its cameras and the other way round. Likewise a receiver skips a list entry whose kind
it does not know (e.g. a new product kind in `products`, §6.3) instead of rejecting the
whole message. Only where a person's input is checked (the public
profile, §6.8) is an unknown key an error. A new message type or command is announced in
`capabilities` (§6.3) or simply not used against an older peer. Cameras in the field may
run an older agent for a long time, so a hub must never require a new field from them.

The WebSocket upgrade and every upload carry `Authorization: Bearer <access_token>`
(§6.2, step 5). The hub closes the WebSocket with:

| Code | Meaning | Agent does |
|---|---|---|
| 4401 | token expired or invalid | gets a new token, reconnects |
| 4403 | device is no longer paired | registers again (§6.2) |
| 4409 | a newer connection of the same device took over | does not reconnect on this socket |

The agent reconnects with exponential backoff (1 s doubling to 5 min, with jitter). The
backoff starts anew only after a connection stayed up for 60 s; a 4401 within that time
waits like any other failure, only an expired token is renewed at once. A 429 or 503 waits
at least its `Retry-After` (seconds, at most 1 h).

### 6.2 Identity and pairing

**Identity.** Each device generates an Ed25519 key pair on first boot. The private key
lives at `/var/lib/allskyhub-agent/device.key` (mode 0600) and never leaves the device;
losing it means pairing again as a new device. The **device id** is the lowercase
RFC 4648 base32 encoding, without padding, of the first 16 bytes of
SHA-256(raw 32-byte public key): 26 characters, `[a-z2-7]`.

**Proof of possession.** The device never relies on its clock to authenticate (no RTC).
It asks the hub for a nonce (`POST /device/v1/challenge`, valid 5 minutes, single use) and
signs the UTF-8 bytes

```
allskyhub-v1\n<purpose>\n<device_id>\n<nonce>
```

with its private key, `purpose` being `register` or `token`. Keys and signatures travel
base64url-encoded without padding.

**Pairing.**

1. The camera gets onto the network: through the app in setup mode (§7.1), with a setup
   file on the SD card (§7.3), or by Ethernet. The app then finds it on the home network
   (§7.2).
2. The agent registers (`POST /device/v1/register` with public key, profile, agent
   version, nonce and signature). While the device is not paired the hub answers with a
   **pairing code**: 6 characters from `ABCDEFGHJKMNPQRSTUVWXYZ23456789` (no 0/O, 1/I/L),
   shown as `ABC-DEF`, valid 15 minutes. Registering again returns the same open code
   until it expires, then a new one. The agent repeats the registration about every 5 s
   until the answer says `paired: true`.
3. The device has no display. The agent exposes the current code on its local setup API
   and web UI (§7). In the normal flow the app reads the code from there and claims it
   through the hub's app API with the user's account, so the user never types it. Typing
   the code into the hub's web UI is the fallback. Claiming binds the device to the
   account. Codes are single use; the hub rate-limits claims per user and per IP and
   answers unknown, expired and used codes the same way.
4. Once paired, registering returns `paired: true` and no code. If a user removes the
   device from their account, the hub closes its WebSocket with 4403 and the next
   registration returns a new code.
5. **Token.** The paired agent gets an access token (`POST /device/v1/token` with device
   id, nonce and signature; valid 1 hour) and uses it as bearer token for the WebSocket
   and uploads. It fetches a new one before expiry or after close code 4401.
6. **Hub keys.** The token answer also carries `hub_keys`, the hub's current Ed25519
   public keys for signing local logins (§7.4): `[{kid, public_key}]`, `public_key`
   base64url without padding. The agent replaces its stored list with every token
   answer. The hub signs with its newest key and keeps an old key in the list for at least
   30 days after rotating. Trust in these keys rests on the TLS connection to the hub, as
   everything else in this protocol does.

### 6.3 Device → hub

- `hello`: device id, profile, agent version, capabilities, and `settings_schema`
  (§6.9): the settings this device has, so that the hub's web UI, the app and the local UI
  build the same settings form from it.
- `status`: mode, last exposure/gain/mean, temperatures, disk, uptime, `time_trusted`
  (§4.4), and `settings`, the current value of every key in `settings_schema` (§6.9;
  at least `{latitude, longitude, timezone, camera, day_delay_s, night_delay_s}`,
  location null until known), and
  `sky` (null until the first frame is measured): `{at, night_id, cloud_cover, sqm_mag,
  stars}` measured on the newest frame. `at` is that frame's `captured_at` and
  `night_id` its night (§4.5); status repeats the last `sky` until a new frame is
  measured, so the hub keeps one sample per `at`. `cloud_cover` (0..1) comes from the
  stars visible in the upper sky at night (sun ≤ −12°), by day (sun ≥ 3°) once
  roadmap L22 has a method that works on real frames, else null. (A blue-to-red ratio
  fails on cameras without an IR cut filter such as the ASI678MC, where an overcast sky
  stays bluish; the method is chosen on the test camera's day frames first, Benjamin,
  2026-10-13.) `sqm_mag` (mag/arcsec², approximate until calibrated) is the
  sky background normalised by exposure and gain, only at astronomical night (sun ≤
  −18°), else null. `stars` is the number of stars detected at night, else null.
  `cloud_map` (optional, roadmap H4; null or missing unless all of: sun ≤ −12°, a trusted
  clock and an orientation, §4.8): cloud cover per sky cell, a list of 24 values, each
  0..1 or null (not judgeable). Cells are 8 azimuth sectors × 3 altitude bands, listed band
  by band from the horizon up and within a band by sector: index = 8·band + sector. Sector
  k (0..7) covers azimuth (k·45° − 22.5°) .. (k·45° + 22.5°), azimuth from north over east,
  so sector 0 is centred on north, 2 on east, 4 on south, 6 on west. Bands: 0 = altitude
  15°..40°, 1 = 40°..65°, 2 = 65°..90°; a direction on a limit belongs to the higher sector
  or band, altitude below 15° to none. A cell's value is 1 − found/expected over the
  catalogue stars down to magnitude 4.0 whose position at the frame's mid-exposure lies in
  the cell and inside the sky mask (§4.7): expected counts them, found those with a
  detected star within 1° of the position (§4.8 lens). A cell is null with fewer than 3
  expected stars (mostly obstructed or too small) and when its centre is within 25° of a
  moon that is at least 25 % lit. The map is not smoothed over time; the hub averages
  if it wants to.
  `transparency_map` (optional, roadmap L26; null or missing whenever `cloud_map` is, and
  until the camera has a photometric reference): how much the sky dims the stars, per sky
  cell of the same 24-cell layout as `cloud_map`, each value the extinction in magnitudes
  (0.00..2.50, 0 = as clear as the camera's clearest nights) or null. The camera measures
  the catalogue stars down to magnitude 5.0 in the cell and inside the sky mask by aperture
  photometry on the measured frame (an aperture of radius 3.5 px of a 3840 px wide frame,
  scaled with the width, centred on the brightest pixel within 4 px of the predicted
  position, the background the median of a ring from 7 to 11 px). Its instrumental
  magnitude −2.5·log10(flux) is normalised by exposure and gain like `sqm_mag`. A star
  without a positive flux counts as 2.50 mag dimmed. A cell's value is the median over its
  stars of (instrumental − slope·catalogue − zp[band]), clipped to 0..2.50, with at least 5
  stars, else null; cells near a lit moon are null as in `cloud_map`.
  The reference `{slope, zp: [band 0, 1, 2], frames, updated_at, width, height}` is in
  `<data>/calibration/photometry.json`. It is fitted on frames with the sun ≤ −18°,
  `cloud_cover` ≤ 0.05 and no moon at least 25 % lit above the horizon: `slope` and `zp`
  per band by least squares over each frame's stars, the reference being the per-band
  90th percentile (the clearest) of the frames of the last 30 nights, after at least 20
  such frames from at least 2 nights. The band term takes up the extinction by airmass, so
  0 near the horizon means "as clear as usual there". A new orientation of another frame
  size, another camera or another profile starts the reference again.
  `transparency` (optional, roadmap L26): the median of the non-null cells of bands 1 and 2
  (above 40°), else null. Both are rounded to 0.01.
  `orientation` (null until solved, §4.8): `{north_deg, mirrored, solved_at, stars,
  rms_deg}`. `north_deg` (0..360) is the image direction of north at the horizon, 0 = up,
  clockwise, like `direction_deg` in events; `mirrored` true means east is
  counter-clockwise from north (the usual view of a camera looking up). The azimuth of
  an image direction d is (north_deg − d) mod 360 when mirrored, else
  (d − north_deg) mod 360; exact at the horizon, approximate above it if the camera
  leans.
  `lens` (optional, may be missing from older agents): `{cx, cy, a1, a3, tilt_east_deg,
  tilt_north_deg, width, height}`, the fitted lens of §4.8 for drawing the sky onto the
  image (constellations on the public page); `width`/`height` 0 means the frame size is
  unknown and the lens must not be used.
  `observatory` (null unless observatory software is configured, §7.5): what the
  telescope next to the camera is doing, relayed from N.I.N.A.'s Advanced API (roadmap
  C15): `{source: "nina", at, connected, target, sequence, last_image, safe}`. `at` is
  when the agent last read it; `connected` false means N.I.N.A. did not answer, and then
  everything else is the last known state or null. `target` (string or null) is the
  current target's name; `sequence` `{name, progress}` with `progress` 0..1, or null;
  `last_image` `{at, filter, exposure_s, hfr, stars}`, or null; `safe` is what the
  camera's own safety monitor (§7.5) says, or null while it is off. Only these metadata
  are relayed, never N.I.N.A.'s images.
  `alpaca_error` (null, or `port_in_use` while `alpaca_enabled` is on but the Alpaca
  server could not start, §7.5).
  `capture_paused` (optional, default false): the owner paused capture (§4.1).
  `reboot_required` (optional, default false): system updates wait for a reboot (§8).
  `adsb` (optional, null unless `adsb_url` is set): the owner's ADS-B receiver (§7.8).
  `update` (null until the updater ran, §8): `{state, version, at, code}` with `state`
  one of `up_to_date`, `downloading`, `waiting` (downloaded, waits for the day),
  `installing`, `installed`, `rolled_back`, `failed`; `version` the release it is about,
  `at` when that state was reached, `code` why it failed or rolled back (e.g.
  `unhealthy`, `checksum`, `bad_signature`, `no_space`).
- `frame`: frame metadata (§4.4), with `bad` (§4.9) null, `dark` or `bright`; the image itself goes over HTTPS when the hub asks
  for it (§6.5, `upload_frame`).
- `event`: a detection (§6.4).
- `event_label` `{night_id, event_id, label, reason, at}`: the owner labelled a detection
  on the camera (§6.4 Labels).
- `aircraft` `{at, aircraft: [{hex, flight, lat, lon, alt_m, track_deg, speed_kn, seen_s}]}`:
  the aircraft the owner's own ADS-B receiver sees (§7.8), at most every 10 s while
  connected, only while `adsb_url` is set. `at` is when the agent read the receiver;
  `hex` the ICAO address (6 lowercase hex digits), `flight` the call sign or null, `lat`/
  `lon` in degrees, `alt_m` the barometric altitude in metres (null if unknown),
  `track_deg` (0..360) and `speed_kn` or null, `seen_s` how old the position is. Only
  aircraft with a position no older than 60 s and within 150 km of the camera, at most
  200, nearest first. Never stored by the hub beyond the matching window (§7.8).
- `night_deleted` `{night_id}`: the owner deleted a whole night on the camera (§7, archive maintenance); the
  hub deletes its copies of that night's frames, products and events too.
- `products`: the night and day products (§5.2) of one night id, `{night_id, products: [{kind,
  name, content_type, size, thumbnail, duration_s}]}` (`size` in bytes; `duration_s`
  only for the two timelapses, else null) with `kind` `keogram`, `startrails`,
  `timelapse`, `day_timelapse` or `day_keogram` (§5.2), `name` `keogram.jpg`,
  `startrails.jpg`, `timelapse.mp4`, `day-timelapse.mp4` or `day-keogram.jpg`, and
  `content_type` `image/jpeg` or `video/mp4`. Sent when a night's products have been
  built, and once after every (re)connect for the newest night that has products. Only
  existing products are listed; the files themselves go over HTTPS when the hub asks for
  them (§6.5, `upload_product`). At most 20 entries; kinds the hub does not know are
  skipped (§6.1).

### 6.4 Events

`{id, night_id, kind, start, end, confidence, has_image, imported, data}` with `kind` one of
`meteor`, `lightning`, `aurora`, `nlc`, `transient`, `satellite`, `clouds`, `sky_quality`.

- `id` is stable on the device: the kind without underscores and the UTC start, e.g.
  `meteor-20261008T214512Z`, with `-2`, `-3` … for further events of the same kind
  starting in the same second. A resent event has the same id; the hub upserts by it.
- `has_image`: the event has a picture, which the hub fetches with `upload_event` (§6.5).
- `imported` (default false): taken over from another program's records (§6.7, roadmap
  L18), not detected by the agent itself; never a push.
- `data` is a flat object of numbers, strings, booleans and nulls. Per kind:

| kind | keys |
|---|---|
| `meteor` | `length_px` (int, track length), `peak` (0..1, peak brightness of the track), `frames` (int, frames it appears in), `direction_deg` (0..360 in the image, 0 = up, clockwise, or null), `shower` (name of a shower, e.g. `Perseids`, or null), `shower_match` (how `shower` was found, below), `aircraft` (optional: the call sign, else the ICAO address, of an aircraft the camera's own receiver matched, §7.8, else null), `trail` (optional, the camera's verdict from the trail itself, §6.4 Trail verdicts; missing means `meteor`) |
| `lightning` | `area_frac` (0..1, part of the sky that lit up), `peak` (0..1, mean brightening of the lit area), `storm_flashes` (int, flashes in the last 30 min including this one), `storm` (string, `storm-` + UTC start of the storm like an event id; the same for all flashes of a storm, a gap of more than 30 min starts a new one) |
| `aurora` | one event per episode (below): `peak_index` (0..100, % of the searched band that is aurora-green in the best frame; the band is the low sky (2–36°) within 70° of the pole's azimuth once the camera has an orientation (§4.8), else the whole low ring of the image circle), `green` (mean green over red of those pixels, 8 bit), `frames` (int, candidate frames so far), `direction_deg` (0..360 in the image, 0 = up, clockwise, of the green's centroid; not a compass bearing), `ongoing` (bool), `image_rev` |
| `nlc` | one episode (below), only with an orientation (§4.8), sun −16° to −9°, in the low sky (12–45°) toward the sun: `peak_index` (0..100, % of that band that is NLC-blue and structured, best frame), `blue` (mean blue over red of those pixels, 8 bit), `frames`, `direction_deg`, `ongoing`, `image_rev` |
| `transient` | one episode (below) per new point of light, §6.4 Transients: `ra_deg`, `dec_deg` (J2000, of its mean position), `mag` (estimated V magnitude from the photometric reference of `transparency_map`), `frames` (int, frames it was found in), `ongoing`, `image_rev` |

`meteor`, `lightning`, `aurora`, `nlc` and `transient` also carry `azimuth_deg` (0..360, compass, 0 = north,
90 = east) and `altitude_deg` (degrees above the horizon) of their position in the sky:
the meteor's midpoint, the centroid of the lit area, of the green or of the blue. The
agent computes them with the orientation (§4.8) valid when it saves the event, so they
stay right after a later re-solve; both are null while the camera has no orientation.

A `meteor` also carries the two ends of its track for triangulation between cameras
(roadmap C4): `start_azimuth_deg`, `start_altitude_deg`, `end_azimuth_deg`,
`end_altitude_deg` (same orientation and nulls as above) and `track_ordered` (bool). A
single frame does not show which way the meteor flew, so `start_*` is where it came from
only when `track_ordered` is true (known from several frames or from its shower's
radiant); otherwise the order is arbitrary. The meteor happened somewhere between the
event's `start` and `end`, which are the frame's exposure (up to a minute at night), so
the hub pairs meteors of two cameras by overlapping intervals and matching tracks, not by
a fixed time difference. The camera fills the track ends only while its clock is
synchronised (`time_trusted`, §4.4), otherwise they are null.

A meteor's `shower` depends on `shower_match`:

- `radiant`: the camera has an orientation (§4.8). `shower` is the active shower whose
  radiant (drifting daily from its peak position) is above −5° altitude and lies within 6°
  of the meteor's path, extended along its great circle beyond either end; a meteor never
  crosses its radiant. If several fit, the closest wins. `shower` null means sporadic.
- `date`: no orientation. `shower` is the strongest shower active on that date, which the
  meteor may or may not belong to.
- null: no orientation and no shower active; `shower` is null too.

Events without `shower_match` (agents before this rule) are treated like `date`.

Some phenomena last minutes to hours (`aurora`, `nlc`). They are one **episode** event: it is
sent when the episode opens and sent again with the same id while it grows (at most
every few minutes) and once more when it ends, with `ongoing` false. `start` stays,
`end`, `confidence` and `data` change.

`image_rev` (int ≥ 1) is in `data` of every event whose picture can be replaced (an
episode's best frame). It is incremented whenever the picture changes; when a resent
event has a higher `image_rev`, the hub fetches the picture again. Events without it
keep their picture.

Events are sent when detected and, after every (re)connect, again for the current
night (§6.7).

**Labels** (Benjamin, 2026-10-13). The owner can mark a detection as `confirmed` or as
`false_positive` with a `reason`: `aircraft`, `satellite`, `artifact` or `other`
(null). A label is `{night_id, event_id, label, reason, at}`, `label` null removes it,
`at` is when it was set; the newer `at` wins. Labels set in the local UI go to the hub as
`event_label` (§6.3), sent when set and, after a reconnect, for every label set while
offline; labels set on the hub go to the camera with the command `label_event` (§6.5),
again after every `hello` until acknowledged. The camera keeps them in
`<data>/events/<night>/labels.json`. A false positive is never deleted: lists hide it
unless asked, and it counts nowhere (night and month statistics, the morning summary,
pushes still to come, exports, triangulation, MQTT counts). Labels do not change the
detectors.

A label can also be set automatically (roadmap H3, §7.8): then it carries `auto: true` and
`detail`, a short text such as the call sign and ICAO address (`"DLH4AB 3c6444"`); both are
optional, default `false` and null, and only an automatic label has them. An automatic label
never replaces a label set by the owner, any label set by the owner replaces an automatic
one whatever its `at`, and between two automatic labels the newer `at` wins (a named
satellite or aircraft from a catalogue replaces the camera's own trail verdict). The UIs
show an automatic label as such ("automatisch: Flugzeug DLH4AB") with one click to undo
it; undoing sets `label` null by the owner, which stays.

**Trail verdicts** (roadmap L25, Benjamin, 2026-10-13; a port of the checks of his Allsky
module Meteor Detection). The camera keeps every streak its meteor detector finds as a
`meteor` event with picture, also those it judges not to be a meteor, and says why in
`data.trail`:

| `trail` | meaning | automatic label |
|---|---|---|
| `meteor` | nothing speaks against a meteor | none |
| `moving` | the streak continues in the frame before or after, moved along its own direction (a satellite or an aircraft) | `false_positive`/`other`, detail `bewegtes Objekt` |
| `circle` | it lies on one great circle (within 1.2°) with a streak of the frame before or after (needs an orientation, §4.8) | as `moving` |
| `repeat` | three or more streaks at the same place (within 55 px) in the 25 min before (hot spot, bloom) | `false_positive`/`artifact`, detail `wiederkehrend` |
| `star_trail` | short and parallel to the stars' motion there (needs an orientation) | `false_positive`/`artifact`, detail `Sternspur` |
| `star` | short, on a catalogue star down to magnitude 5 (scintillation; needs an orientation) | `false_positive`/`artifact`, detail `Stern` |
| `glare` | a radial spike next to a saturated light (moon, lamp) | `false_positive`/`artifact`, detail `Blendung` |
| `dashed` | its brightness along the track is broken into ten or more dashes (blinking aircraft lights, a tumbling satellite) | `false_positive`/`aircraft`, detail `blinkt` |
| `fragmented` | five or more pieces in line beyond its ends | `false_positive`/`other`, detail `Bruchstücke` |
| `edge_glow` | both ends within 50 px of the sky mask's edge, at least 80 px long and broad (elongation under 10) | `false_positive`/`artifact`, detail `Randleuchten` |

The checks run in this order, as in the Allsky module, and the first that applies is the
verdict; the thresholds are the module's (in pixels of the stored frame; the glare check
scales with the frame size), `star` and `star_trail` only for streaks up to 130 px. A streak
without its disappearance at the same place in the next frame (flicker), one whose light
is there in the frames before and after too (a drifting star) and every streak of a
cloudy or twinkling frame are dropped without an event, as before. The camera sets the
automatic label when it saves the event and sends it as `event_label` (§6.3).

The pieces of one moving object (`moving` or `circle`) in frames at most 5 minutes apart,
each continuing the previous piece or at the same place (a drifting contrail), are one
event (Benjamin, 2026-10-13): it is sent again with the same id as it grows, `end` and
`data.frames` grow, `end_*` is the end of the newest piece farther from `start_*`, and the
picture stays the first piece's. At most 300 non-`meteor`
events are kept per night (a growing one counts once); beyond that they are only counted in the log. Nothing is deleted:
the owner sees them with "Aussortierte zeigen" and can label any of them a meteor.

**Transients** (roadmap L27): a new star-like point of light that stays fixed among the
stars, such as a nova or a bright comet's head. It needs an orientation (§4.8) and the
photometric reference of `transparency_map`, sun ≤ −15° and a trusted clock. On each
measured frame the camera takes the detected stars (the same detection as `stars`) above
20° altitude, at least 50 px inside the sky mask's edge, and estimates their magnitude with
the reference. A candidate is one of magnitude 3.5 or brighter with, within 0.5°, none of:
a Hipparcos star to Vmag 7.0 or a Hipparcos variable whose brightest magnitude (Hpmax)
is 4.0 or brighter, a planet Mercury to Saturn, the moon. It becomes a `transient` event
when the same position (within 0.3° in RA/Dec) is a candidate in at least 10 frames over
at least 20 minutes of the same night: in that time the sky turns by 5°, so a hot pixel or
a lamp, fixed in the image, and a satellite or an aircraft, moving faster than the stars,
never make one. Its picture is a crop of 300 × 300 px around it from the best frame. The
event is an episode: sent again as more frames find it, `ongoing` false after 30 minutes
without it. At most 3 per night; `detect_transient` `off` runs no check, `low`/`high` move
the magnitude limit to 3.0/4.0. The hub decides what is worth a push (roadmap H7).

### 6.5 Hub → device

`command`: `set_settings`, `focus_mode` (`{on: bool}`, §7), `restart`, `update`,
`upload_frame`, `upload_product`, `upload_event`, `local_access`, `label_event`. Each command is acknowledged with `ack` or `error` (`code` one of
`not_found`, `invalid_args`, `unsupported`, `failed`).

**`set_settings`** with a partial object of keys from `settings_schema` (§6.9), for
example `latitude` and `longitude` (both or neither), `timezone` (IANA), `camera`
(`auto`, `zwo-asi678mc`, `rpi-hq`, `sim`), `day_delay_s` and `night_delay_s` (0..3600 s
between frames). Only the given keys change: a key that is left out keeps its value, and
`null` resets a `nullable` setting ("not set"). An unknown key or a value the schema does
not allow changes nothing and is answered with `error` `invalid_args`, whose `message`
names the fields, comma-separated. The device validates; the hub may check against the
schema first to show errors at once. On success the device stores the settings, answers `ack` and restarts its
capture with them; its WebSocket reconnects within about 15 s.

**`upload_frame`** `{night_id, name, variant}` with `variant` `full` (default) or
`thumb`: the device uploads that image with
`PUT /device/v1/frames/{night_id}/{name}?variant=<variant>` (`image/jpeg`) and then
answers `ack`. If the frame no longer exists (retention, §4.5) it answers `error` with
`not_found`. The hub decides what to fetch and how often, for example the latest image
at most every few minutes, thumbnails for the app's gallery, and every frame in full
while someone watches the live view. The device needs no upload policy of its own.

**`upload_product`** `{night_id, name, variant}`, `variant` as for `upload_frame`: the
device uploads that night product (§5.2) with
`PUT /device/v1/products/{night_id}/{name}?variant=<variant>`, streamed with
`Content-Length`; the content type is the product's (`image/jpeg` or `video/mp4`) for
`full` and `image/jpeg` for `thumb` (the timelapse's thumbnail is a frame from the
middle of the night). Then it answers `ack`, or `error` with `not_found` if the product
does not exist (any more). The hub decides what to fetch, for example every thumbnail
right away and the full product when someone opens it.

**`upload_event`** `{night_id, event_id, variant}`, `variant` as for `upload_frame`: the
device uploads the event's picture with
`PUT /device/v1/events/{night_id}/{event_id}?variant=<variant>` (`image/jpeg`), then
answers `ack`, or `error` with `not_found` if the event has no picture or it is gone.

**`local_access`** `{verifier}`: the verifier of the camera's offline code (§7.4), a PHC
string `$argon2id$v=19$m=19456,t=2,p=1$<salt>$<hash>`, or `null` to remove it. The device
answers `error` `invalid_args` for any other algorithm and for parameters below m=19456,
t=2 or above m=65536, t=10, p=4 (more would cost the camera's memory or minutes per try).
The camera runs one check at a time and counts a try before it runs. The device
stores it (replacing an older one), drops all local sessions that were opened with the
offline code, and answers `ack`. The hub sends it when the owner creates or removes the
code and, until the device has acknowledged it, again after every `hello`. The hub never
stores the code itself, only the verifier until it is delivered.

**`label_event`** `{night_id, event_id, label, reason, at}` (§6.4 Labels): the device
stores the label unless it has a newer one for that event and answers `ack`, or `error`
`not_found` if it does not know the event. An older device answers `unsupported`; the
hub keeps the label for itself.

### 6.6 Device HTTP endpoints

All bodies are JSON (models in `packages/protocol`, `allskyhub_protocol.device_api`)
except the image upload.

| Endpoint | Auth | Body → answer |
|---|---|---|
| `POST /device/v1/challenge` | none | `{device_id}` → `{nonce, expires_in}` |
| `POST /device/v1/register` | signature (`register`) | `{public_key, profile, agent_version, nonce, signature}` → `{device_id, paired, pairing_code?, expires_in?}` |
| `POST /device/v1/token` | signature (`token`) | `{device_id, nonce, signature}` → `{access_token, token_type: "bearer", expires_in, hub_keys}` |
| `GET /device/v1/ws` | bearer | WebSocket (§6.1) |
| `PUT /device/v1/frames/{night_id}/{name}?variant=` | bearer | `image/jpeg` → 204 |
| `PUT /device/v1/products/{night_id}/{name}?variant=` | bearer | `image/jpeg` or `video/mp4` → 204 |
| `PUT /device/v1/events/{night_id}/{event_id}?variant=` | bearer | `image/jpeg` → 204 |
| `GET /device/v1/public` | bearer | → `{enabled, url, profile}` (§6.8) |
| `PUT /device/v1/public` | bearer | `{enabled?, profile?}` → `{enabled, url, profile}` (§6.8) |

Errors are HTTP status codes with `{"detail": "..."}`: 400 invalid body, 401 bad
signature, nonce or token, 403 device not paired (token), 404 unknown upload (the hub
did not ask for this frame), 413 image too large, 422 invalid public profile (§6.8),
429 rate limited (with `Retry-After`).

### 6.7 Offline behaviour

The device does not queue uploads or WebSocket messages while the hub is unreachable;
capture and storage go on (§1, goal 4). After reconnecting it sends `hello`, a fresh
`status`, the newest night's `products` and the current night's `event`s, and the hub
requests whatever it wants with `upload_frame`, `upload_product` and `upload_event`.

**Imported nights.** After the agent took over another program's pictures (roadmap L18),
it announces each imported night once: its `products` and its `event`s (with
`imported: true`, §6.4), one night per second, carried on across reconnects until all are
through. Frames of imported nights stay on the camera (local UI, §7). The hub stores them
like any other night (upsert per night and name or id) and never sends a push for an
imported event.

An import takes over one camera; if the other program's records hold several, the user
picks one. A timelapse that is not H.264 MP4 (§5.2) is left out, not transcoded
(Benjamin, 2026-10-13).

### 6.8 Public page

Each camera can have a public page on the hub (`/sky/<slug>`, off by default). The owner
manages it in the hub's web UI and, through these endpoints, in the camera's local UI
(§7.4). The hub is the only place it is stored; the last write wins.

`GET /device/v1/public` → `{enabled, url, profile}`: `url` is the page's address or
`null` while it is off. `PUT /device/v1/public` with `{enabled?, profile?}`: `enabled`
switches the page (switching off makes the old address invalid at once; switching on
again gives a new one); `profile`, if given, replaces the whole profile, and missing
fields take their defaults. A `PUT` with only `profile` leaves `enabled` and the address
unchanged, and one with only `enabled` leaves the profile unchanged. The answer is the new
state.

`profile`: `title` (≤ 100 characters), `location_label`, `camera`, `lens`, `computer`,
`owner` (each ≤ 200), `notes` (≤ 2000), all strings, empty = not shown;
`show_coordinates` (bool, default false: the coordinates appear rounded to 0.1°);
`sections`: `{live, loop, archive, events, sky, aurora}`, booleans, default true, and
`observatory` (§7.5), default false, and `aircraft` (§7.8), default true. An
unknown key, a wrong type or a text that is too long is answered with 422 and a German
sentence in `detail` that the local UI shows as it is. Models: `PublicProfile`,
`PublicState`, `PublicUpdate` in `allskyhub_protocol.device_api`.

### 6.9 Settings schema

The device describes its settings in `hello.settings_schema`, a list in display order.
The hub's web UI, the app and the local UI (§7) render their settings forms only from
this list, so a new setting needs a new agent but no new hub or app. The hub keeps the
newest schema per device; while a device is offline its last schema and `status.settings`
are shown read-only.

Each entry:

| Field | Meaning |
|---|---|
| `key` | name in `status.settings` and `set_settings`, `^[a-z][a-z0-9_]{0,39}$` |
| `type` | `number`, `int`, `bool`, `choice`, `string` or `location` |
| `label` | label, ≤ 60 characters |
| `group` | section heading, ≤ 40 characters; entries of one group are shown together, groups in order of first appearance |
| `help` | optional sentence under the field, ≤ 300 characters |
| `unit` | optional unit shown after the value (`s`, `ms`, `%`, `°`), ≤ 10 characters |
| `min`, `max`, `step` | `number` and `int` only, optional; values outside `min`..`max`, NaN and infinity are `invalid_args`; `step` is only a hint for the form and not checked |
| `choices` | `choice` only: `[{value, label}]`, `value` a string, `label` a text as above |
| `max_length` | `string` only, default 200 |
| `nullable` | the value may be `null` ("not set": the device's default, which `help` names and which can be lower than `max`, e.g. 60 s for `night_max_exposure_s` with `max` 300); default false |
| `write_only` | a secret such as an API key; default false. `status.settings` never contains it, only `<key>_set` (bool). Forms show an empty password field with "gesetzt" or "nicht gesetzt"; an empty field leaves the value as it is, `null` in `set_settings` deletes it |

**Texts** (`label`, `group`, `help`, `choices[].label`) are either a string, which is
German, or `{"de": "...", "en": "..."}` with `en` optional. Forms show the user's language
and fall back to German.

**A usable schema** has every key once and no `latitude` or `longitude` entries of its own
(they belong to `location`). The hub checks this (`schema_problems`) and keeps the
previous schema of the device if the new one is not usable.

Values in `status.settings` and `set_settings`: JSON number for `number`, integer for
`int`, boolean for `bool`, one of the `choices` values for `choice`, string for `string`.
`location` is the one compound type: the entry has the key `location` and stands for the
two keys `latitude` (−90..90) and `longitude` (−180..180), which are reported and set
together as before; the forms show it as a map with a coordinate field.

`settings_schema` may be empty for an old agent; the hub then shows the fixed fields
listed under `set_settings` (§6.5) and checks them with `SetSettingsArgs` only, not with
`invalid_settings`. The hub must accept `settings_schema` and further keys
in `status.settings` before agents send them (deploy the hub first). Models and checks:
`SettingSpec`, `SettingChoice`, `SettingType`, `LabelText`, `GroupText`, `HelpText`,
`localized`, `schema_problems` and `invalid_settings` in `allskyhub_protocol.models`.

## 7. Local web UI

A small mobile-first page served by the agent on the local network (default port 8080,
plain HTTP): live image, status, focus helper and, while unpaired, the pairing code. It
also works without any hub.

**Focus mode.** Shows a sharpness score (variance of the Laplacian on the central crop,
higher is sharper), the best value since focus mode started, and a 1:1 crop of the
centre. Frames are not stored, follow each other without delay, and exposure is capped
at 2 s with the rest moved into gain (§4.3), so feedback comes every few seconds even at
night. The hub switches it with the `focus_mode` command, args `{on: bool}` (§6.5).

**Setup API** for the app's onboarding (§6.2 step 3); the app polls it about every 2 s
until `paired` is true:

`GET /api/setup` → `{device_id, hub_url, profile, agent_version, paired, pairing_code,
expires_in, connected, setup_mode, last_error, location_set, timezone, camera}`, where `pairing_code` is the raw 6-character code (no hyphen)
or `null` once paired or before the first registration, `expires_in` counts down in
seconds, and `connected` says whether the WebSocket to the hub is open.

Before pairing the local UI has no authentication and is meant for the local network
only; setup mode (§7.1) stays limited to it. After pairing it needs a local login
(§7.4).

**Language and units.** `language` (`de` default, `en`) and `temperature_unit` (`c`
default, `f`) change only how the local UI shows texts and temperatures; the protocol
stays in °C. Setting texts are already `{de, en}` (§6.9).

**Overlay** (roadmap L11). The owner arranges what is drawn over the image: for each of
the four corners a list of fields from `time`, `date`, `exposure`, `gain`,
`sensor_temp`, `sun`, `moon`, `cloud_cover`, `sqm`, `stars`, `mode` and up to two own
text lines (≤ 60 characters each, e.g. the station's name), a size (`small`, `normal`,
`large`) and whether to draw the compass and the constellations. It is kept in
`<data>/overlay.json` (`GET/POST /api/overlay`), the same for every browser, and used
for the live image, the lightbox and "save with overlay". It is still only drawn over
the image, never into stored frames.

**Archive maintenance.** The owner can delete a whole night except the current
one (`POST /api/nights/<id>/delete`, after a confirmation in the UI); the agent removes
the folder and its events and sends `night_deleted` (§6.3), again after a reconnect if
the hub was not reachable.

**Camera check.** `GET /api/check` lists checks, each `{id, state, detail}` with `state`
`ok`, `warn` or `fail`: power (no under-voltage), clock synchronised, free disk, frames
coming, sharpness against the best seen, sky mask learned, orientation solved, hub
connected. The UI shows them as a traffic light with what to do.

### 7.1 Setup mode

The camera opens its own Wi-Fi network for setup:

- **When:** on start without any configured Wi-Fi and without Ethernet, or when the
  configured Wi-Fi has been unreachable for 2 minutes and there is no Ethernet.
- **Network:** Wi-Fi `allskyhub-XXXX` with WPA2-PSK, where `XXXX` is the first four
  characters of the device id in upper case, and the camera's **setup key** as passphrase
  (Benjamin, 2026-10-13: an open network let anyone nearby read the home Wi-Fi password
  or set the camera up first). The camera is `10.42.0.1`, hands out addresses by DHCP
  and answers every DNS query with `10.42.0.1` (captive portal). The local web UI is at
  `http://10.42.0.1:8080/`; port 80 redirects there.
- **End:** after the camera has joined a network, or after 15 minutes without a request.
- **Setup key:** 12 characters from the pairing alphabet, shown as `XXXX-XXXX-XXXX` (the
  passphrase is the 12 characters without hyphens). It lives in
  `/boot/firmware/allskyhub-setup-key.txt` and stays there; the camera makes a random one
  on first boot if the file is missing or unusable. A camera that is sold carries it on a
  label as the standard Wi-Fi QR code `WIFI:T:WPA;S:allskyhub-XXXX;P:<key>;;`, which the
  phone's camera and the app both read; the app also takes it typed. Whoever flashes the
  image can write the file before the first boot (any 12 characters from the alphabet),
  read the made one from the SD card, or skip the setup network with a setup file (§7.3).
  `GET /api/setup` never shows it.
- **QR code:** the ZXing Wi-Fi format, fields in any order, `H:` optional; the camera
  writes exactly `WIFI:T:WPA;S:allskyhub-XXXX;P:<12 characters>;;`. The app accepts `\`
  escapes (`\\`, `\;`, `\,`, `\:`, `\"`) when it reads one, although the camera's SSID
  and key never need them, and only `T:WPA` with an SSID starting `allskyhub-`. A camera
  with an agent from before this rule opens its setup network without a key; the app
  joins such an open `allskyhub-XXXX` network without asking for a key and shows that
  this camera should be updated.

Endpoints, only reachable from the setup network:

| Endpoint | Body → answer |
|---|---|
| `GET /api/setup` | as in §7, plus `setup_mode: true` and `last_error` |
| `GET /api/wifi/networks` | → `[{ssid, signal, secure}]`, strongest first, hidden networks left out |
| `POST /api/setup/network` | `{ssid, password?, country, hub_url?, latitude?, longitude?, timezone?}` → `202 {will_join: ssid}` |

- `country` is the ISO 3166 code for the radio rules (set by a root helper of the image before joining); `hub_url` overrides the configured
  hub (default `https://allskyhub.org`) and must be `https` (`400` otherwise; the token
  for the hub must never go over plain `ws`). The SSID and the password must not contain
  control characters.
- `latitude` and `longitude` (degrees, both or neither) and `timezone` (IANA name, e.g.
  `Europe/Vienna`) come from the phone. The camera needs the location for day and night
  (§4.2) and the time zone for its night folders (§4.5); it only starts capturing once it
  has a location. They are stored with the camera's settings once it has joined.
- After answering `202` the camera leaves setup mode and joins the network. If that fails
  it opens the setup network again and `/api/setup` reports `last_error` as
  `wifi_auth`, `wifi_not_found` or `no_internet` (otherwise `null`).
- The Wi-Fi password is stored only in the system's network configuration, never in
  logs and never sent to the hub.
- **Paired cameras.** Setup mode also starts on a paired camera (its Wi-Fi gone for 2
  minutes). Then `GET /api/wifi/networks` and `POST /api/setup/network` need a local session
  (§7.4), like everything else; `GET /api/setup` stays open. In the setup network there is
  no internet, so only the offline code works. Without a session nobody nearby can move
  the camera to another network or another hub. A camera without an offline code can be
  reached again on its old network, through Ethernet or with a setup file (§7.3).
- **Another Wi-Fi from the normal network** (roadmap L13): where the agent manages the
  network, the local UI can also switch the Wi-Fi without setup mode: `GET /api/network/wifi`
  (as above) and `POST /api/network/wifi` with `{ssid, password?, country}` only, never
  hub or location, the same checks, `202 {will_join}`, and the same fallback to setup mode
  if joining fails. Once paired both need a local session (§7.4).

### 7.2 Discovery

On every network the camera announces itself by mDNS: host name `allskyhub-xxxx.local`
(same four characters, lower case) and the DNS-SD service `_allskyhub._tcp` on the local
web UI's port with the TXT records `id=<device id>` and `v=1`. After sending the network,
the app rejoins the home Wi-Fi, browses `_allskyhub._tcp`, picks the entry whose `id`
it saw in setup mode, and goes on polling `/api/setup` there until `paired` is true.
Typing the camera's address is the fallback.

### 7.3 Setup file

For setting up without the app (and for cameras on Ethernet with a fixed hub): a file
`allskyhub-setup.json` on the SD card's boot partition (`/boot/firmware/`):

```json
{"allskyhub_setup": 1, "wifi": {"ssid": "Home", "password": "secret"},
 "wifi_country": "AT", "hub_url": "https://allskyhub.org",
 "location": {"latitude": 48.14, "longitude": 14.39}, "timezone": "Europe/Vienna",
 "camera": "auto"}
```

All keys except `allskyhub_setup` are optional. `hub_url` must be `https`. `camera` is `auto` (default: a ZWO
camera if one is connected, else a Raspberry Pi camera), `zwo-asi678mc`, `rpi-hq` or `sim`
(a simulated camera, for testing an image without a camera). The file is read once at
boot, applied (Wi-Fi as a NetworkManager connection, country, hub, location, time zone,
camera) and deleted. An unusable file is renamed to `allskyhub-setup.failed.json` and not tried
again.

### 7.4 Local login

**Addresses** (security review, Benjamin 2026-10-13). Against DNS rebinding the local UI
answers only requests whose `Host` (without port) is an IP address, `localhost`, a name
ending in `.local`, a name without a dot (a short name from the home router), or one of
the names in the setting `local_hostnames` (a list of host names, default empty, e.g.
`allsky.fritz.box`); also every request in setup mode (the setup network has no other
sites) and every request from 127.0.0.1 or ::1 (a reverse proxy on the same machine,
like the one for a public domain). Any other `Host` gets `421` with a short page that
names the setting. A browser `POST` whose `Origin` is not the address the UI was reached
under is refused with `403` (cross-site request forgery); requests without `Origin`
(the app, scripts) are not affected. No page may be framed (`X-Frame-Options: DENY`).

**Unpaired**, nothing changes: no login. **Paired**, every page and API of the local UI
needs a local session, except `GET /api/setup` (no secrets; the app's onboarding polls
it), `GET /api/health` (version and frame count for the updater and the watchdog, §8),
the login routes below and the static files. There are two ways to log in; both
open the same session.

**A: with the allskyhub account (single sign-on through the hub).**

1. The agent creates a nonce (32 random bytes, base64url), keeps it with the time it was
   made (monotonic clock) for 5 minutes, sets it as cookie `ash_login_nonce` (HttpOnly,
   SameSite=Lax, Max-Age 300) and redirects to
   `<hub>/cameras/<device_id>/local-login?return=<base>&nonce=<nonce>`, where `<base>` is
   the absolute URL under which the browser reached the local UI, ending in `/` (for
   example `https://cam.example.org/hub/`). The agent builds it from the `Host` header and
   the connection's scheme. Only when the connection comes from 127.0.0.1 or ::1 (a
   reverse proxy on the same machine) does it take the scheme from `X-Forwarded-Proto`
   and a path prefix from `X-Forwarded-Prefix`. Without a prefix header the path is `/`.
2. The hub requires a web login (and returns to this step afterwards) and that the
   camera belongs to the account; otherwise 404. `return` must be an absolute `http` or
   `https` URL without user info, query or fragment, at most 500 characters. The hub
   shows the camera's name and the target origin and asks for confirmation (any origin is
   allowed, since cameras sit behind addresses the hub cannot know).
3. After confirmation the hub navigates the browser top-level to
   `<base>local-login/callback#assertion=<assertion>`. Because it is in the fragment, the
   assertion reaches no server and no log. The agent answers this GET with a small page
   whose own script reads the fragment, removes it with `history.replaceState` and sends
   it same-origin as a form `POST` to `<base>local-login/callback` (field `assertion`).
   That request carries the `SameSite=Lax` nonce cookie. A cross-site POST from the hub
   would not carry it.
4. The assertion is `<payload>.<signature>`: `payload` is the base64url-encoded JSON
   `{kid, aud, sub, nonce, iat, exp}` (`aud` the device id, `sub` the user id, `iat` and
   `exp` Unix seconds, `exp` at most 60 s after `iat`); `signature` is base64url of the
   Ed25519 signature with key `kid` over the UTF-8 bytes
   `allskyhub-v1\nlocal-login\n<payload>`.
5. The agent accepts it if `kid` is in its hub keys (§6.2), the signature is valid, `aud`
   is its own id, `nonce` equals the cookie and a nonce it made less than 5 minutes ago
   (each nonce once), and, if its clock is trusted (§4.4 `time_trusted`), `exp` has not
   passed. It then opens a session.

**B: offline code**, for when the hub cannot be reached. In the hub's web UI the owner
creates a code for the camera: 12 characters from the pairing alphabet (§6.2), shown once
as `XXXX-XXXX-XXXX`, then only replaceable or removable. The hub sends the camera only an
Argon2id verifier (`local_access`, §6.5), deliberately not anything derived from the
account password, so a stolen camera allows no offline attack on it. The local login
form takes the code with or without hyphens, in any case. The agent allows 5 failed
attempts per 15 minutes in total and then refuses until that time has passed. This is
deliberate: someone who can reach the local UI (for example through a public reverse
proxy) can block the offline code for a while. The lockout does not affect login A.

**Session.** Cookie `ash_session` with 32 random bytes, HttpOnly, SameSite=Lax, Max-Age 30
days, `Secure` when the request came over HTTPS (also `X-Forwarded-Proto: https` from a
connection from 127.0.0.1 or ::1, as for `<base>`). The agent stores only a hash of it,
with the way of login and the time, persistently in its data directory (mode 0600), so
sessions survive restarts and updates. Logging out deletes it.

**Unpairing.** When the hub says the device is no longer paired (close code 4403 or
`paired: false`), the agent deletes all sessions, the offline verifier and the hub keys.

**Public page.** The local UI manages the public page (§6.8) through the agent, which
forwards to `GET` and `PUT /device/v1/public`. Writing needs a session. While the hub is
unreachable the local UI shows the last known state read-only, with the note "Hub nicht
erreichbar". On 422 it shows the hub's `detail`.

### 7.5 ASCOM Alpaca (observatory software)

So that N.I.N.A., SGP, Voyager and other ASCOM clients can use the camera as a safety
monitor and sky sensor without a plugin, the agent can run an
[ASCOM Alpaca](https://ascom-standards.org/api/) server. It is off by default and switched
with the settings `alpaca_enabled` and `alpaca_port` (default 11111, §6.9). It only
reports and changes nothing on the camera, so it needs no local login (§7.4). Like
Alpaca in general it has no authentication, so it is meant for the local network only.
It never runs in setup mode (§7.1).

**Discovery.** The agent answers the Alpaca discovery broadcast on UDP port 32227: a
datagram that starts with `alpacadiscovery1` gets the reply `{"AlpacaPort": <port>}`,
sent unicast to the sender's address and port (IPv4 broadcast; IPv6 multicast
`ff12::a1:9aca` if the system has IPv6). If `alpaca_port` or UDP 32227 is already taken
(for example by ASCOM Remote), the server does not start; `status` reports
`alpaca_error` (`port_in_use`) and the local UI shows it.

**Management API** (`/management/apiversions` → `[1]`, `/management/v1/description`,
`/management/v1/configureddevices`) lists two devices with stable `UniqueID`s derived
from the device id: `SafetyMonitor` 0 and `ObservingConditions` 0, both named
"allskyhub <first four characters of the device id in upper case>".

**Device API** `/api/v1/{safetymonitor|observingconditions}/0/{member}` as specified by
ASCOM (Platform 7, `InterfaceVersion` 3 for SafetyMonitor, 2 for ObservingConditions):
`GET` with query parameters, `PUT` form-encoded, parameter names case-insensitive, every
answer `{Value, ClientTransactionID, ServerTransactionID, ErrorNumber, ErrorMessage}`.
`ClientID` and `ClientTransactionID` may come in the query or the form body;
Query parameter names of a GET are case insensitive, those of a PUT body are not (`AveragePeriod`, `Connected`, `ClientTransactionID` exactly); a PUT without its parameter is answered with HTTP 400. The server keeps connections open (HTTP/1.1). `ClientTransactionID` is returned unchanged (0 if missing or not an unsigned 32-bit
number), `ServerTransactionID` counts up per process (unsigned 32-bit, wrapping).
`connected`, `connect`, `disconnect`, `connecting`, `description`, `driverinfo`,
`driverversion`, `interfaceversion`, `name`, `supportedactions` (empty) and
`devicestate` work for both. `devicestate` lists `[{Name, Value}]` with `IsSafe` for the
SafetyMonitor and `CloudCover` and `SkyQuality` (only those with a value) for
ObservingConditions, plus `TimeStamp`; `TimeSinceLastUpdate` is not an operational
property and stays out (ConformU 4.5.0, 2026-10-13). `action`, `commandblind`, `commandbool`
and `commandstring` and the interface members named below as not implemented answer
`0x400` (not implemented). A path that is not a member of the interface, or not all
lowercase (`/api/v1/SAFETYMONITOR/0/…`), is answered with HTTP 400 and a plain-text body, as
Alpaca specifies. Reading a property other than
`connected`, `connecting` and the descriptive ones while not connected answers `0x407`
(not connected).

**SafetyMonitor `issafe`.** True only while all of these hold:

- The newest frame is at most 10 minutes old and its `status.sky` has a `cloud_cover`, that is,
  it is night (sun ≤ −12°, §6.3). In the day and in twilight the monitor says unsafe.
  This is what observatory software expects: flats and twilight work run without a safety
  condition, and only the night's imaging waits for "safe".
- `cloud_cover` is below `safe_cloud_max` (setting, default 30 %). To turn unsafe it has
  to be at or above that on two frames in a row; to turn safe again it has to be at least
  10 percentage points below for `safe_hold_min` minutes (setting, default 10).
- No `lightning` event in the last 30 minutes (§6.4). Rain counts the same way once a
  rain detector reports it.

Stale or missing data is unsafe; `issafe` never fails with an error once connected.

**ObservingConditions.** `cloudcover` in percent (0..100) and `skyquality` in mag/arcsec²
from `status.sky` (§6.3); `skybrightness`, `starfwhm` and the weather members
(`dewpoint`, `humidity`, `pressure`, `rainrate`, `skytemperature`, `temperature`,
`winddirection`, `windgust`, `windspeed`) answer `0x400`. While a value is not
available (day, twilight, no frame yet) the member answers `0x402` (value not set)
rather than an invented number; ConformU must accept this, otherwise the implementation
follows what ConformU expects and this section is corrected. `timesincelastupdate` gives the age of the newest
measured frame in seconds (`-1` if none), also with an empty or matching
`sensorname`; `sensordescription` names the measurement ("Sternzählung der
Allsky-Kamera", "Himmelshintergrund, normiert"). `averageperiod` reads 0 and accepts
only 0 (other values `0x401`); `refresh` does nothing.

Implementations are checked with ASCOM ConformU.

**Relaying N.I.N.A.** With the settings `nina_url` (for example
`http://192.168.1.30:1888`, empty = off) the agent reads the
state of N.I.N.A.'s Advanced API plugin every 30 s on the local network and reports it as
`status.observatory` (§6.3). It only reads; it never controls N.I.N.A. The Advanced API has no
authentication, so there is no key setting (Benjamin, 2026-10-13). Target names and
sequences can be private: the hub shows `observatory` to the owner and the camera's
members, and on the public page only with the section `observatory` (§6.8, default
false).

### 7.6 HTTPS

Roadmap L24, setting `https` (default off). When on, the local UI is also served over
HTTPS on port 8443 with a certificate the agent makes on first use (ECDSA P-256,
self-signed, valid 10 years, subject alternative names: the host name, `<host>.local` and
the current IP addresses; renewed when an address changes), kept in `<data>/tls/` (key
mode 0600). The owner can upload an own certificate and key (PEM) in the local UI
instead; a certificate that does not match its key or has expired is refused. Plain HTTP
on 8080 then answers every page with a redirect to HTTPS, except `/api/setup` and
`/api/health`, which the app's onboarding (§7), the updater (§8) and the watchdog (§8.1)
use, everything in setup mode (§7.1, the setup network), and a reverse proxy on the same
machine (a request from 127.0.0.1 or ::1 with `X-Forwarded-Proto`), which does its own
TLS. `GET /api/tls` shows whether HTTPS is on, whose certificate is used, until when and
its SHA-256 fingerprint; `POST /api/tls` `{cert, key}` installs the owner's pair (400
with `bad_certificate`, `bad_key`, `key_mismatch`, `expired` or `too_large`),
`POST /api/tls/remove` goes back to the camera's own; both restart the agent. An own
certificate that has expired is no longer used. Browsers warn about a self-signed certificate once; the session cookie is `Secure`
over HTTPS (§7.4).

### 7.7 MQTT

Roadmap L23, for home automation (Home Assistant and others). Settings: `mqtt_host`
(empty = off), `mqtt_port` (default 1883), `mqtt_tls` (default off), `mqtt_user`,
`mqtt_password` (`write_only`, §6.9) and `mqtt_prefix` (default `allskyhub`). The agent
connects to the broker on the local network and publishes, with `<base>` =
`<prefix>/<device id>`:

- `<base>/availability`: `online`, retained; the broker's last will sets `offline`.
- `<base>/state`, retained, at most once a minute and after every mode change: JSON
  `{at, mode, exposure_us, gain, mean, sensor_temp_c, cpu_temp_c, cloud_cover, sqm_mag,
  stars, safe}` with the meanings of §6.3 (`cloud_cover` in percent, `safe` the
  safety monitor's verdict of §7.5 or null while it is off).
- `<base>/event`, not retained: every detection (§6.4) as `{kind, at, night_id, ...}`
  without images.
- Home Assistant discovery: one retained config per value under
  `homeassistant/sensor/<device id>/<key>/config` (and `binary_sensor` for `safe`),
  with the device's name and id, so the camera appears as one device.

The agent only publishes; it subscribes to nothing and cannot be controlled over MQTT.
Changing `mqtt_host` or `mqtt_port` deletes the stored password unless the same change
sets a new one, so nobody (a hub included) can send the password to another broker.
A broker that cannot be reached is retried with backoff and never slows capture.

### 7.8 Aircraft (ADS-B)

Roadmap H3 (Benjamin, 2026-10-13): aircraft are labelled over the live image, and meteor
detections that are an aircraft are marked. Two sources:

- **adsb.lol** (hub, always): adsb.lol (ODbL, no key) gives only current positions, so the
  hub fetches the aircraft within 150 km of a camera every 10 s while that camera is
  online and has a location, and either it is night there (sun below −6°) with meteor
  detection not off, or someone watches its live image; cameras within 150 km of each
  other share one request. The credit "Aircraft data: adsb.lol (ODbL)" is shown wherever
  aircraft are drawn.
- **the owner's receiver** (agent, optional): setting `adsb_url`, empty = off, the URL of a
  readsb, dump1090-fa or tar1090 `aircraft.json` on the local network (for example
  `http://192.168.1.40/tar1090/data/aircraft.json`). The agent reads it every 5 s, keeps
  the last 10 minutes of positions in memory, relays them as `aircraft` (§6.3) and uses
  them for its own meteor check below, also while the hub is unreachable. A receiver that
  does not answer is retried and never slows capture; `status.adsb` is `{at, aircraft,
  error}` (when it was last read, how many aircraft, null or `unreachable`/`bad_data`),
  null while `adsb_url` is empty.

The hub merges both per ICAO address: the receiver's position wins while it is at most 30 s
old, else adsb.lol's.

**Matching a meteor.** Only with an orientation (§4.8) and a trusted clock (§4.4). For each
aircraft the position is interpolated over the event's `start − 30 s .. end + 30 s` (ADS-B
latency and exposure), turned into altitude and azimuth from the camera (camera height
taken as sea level, altitude from `alt_m`), giving the aircraft's apparent path. A meteor
is an aircraft when the aircraft is above 5° altitude for part of that window and both ends
of the meteor's track (§6.4 `start_*`/`end_*`) lie within 2° of that path. The nearest such
aircraft is named.

- The agent, with its own receiver, checks every meteor when it saves it: a match fills
  `data.aircraft` and sets an automatic label `false_positive`/`aircraft` (§6.4 Labels),
  which goes to the hub as `event_label`. The event is still saved and sent.
- The hub checks every meteor that has no label set by the owner against the merged positions once
  `end + 60 s` has passed, but only when its kept positions cover the whole window
  `start − 30 s .. end + 30 s`; otherwise (backlog after an outage, a camera offline, an
  imported event, §6.4) the event stays unchecked. A match sets an automatic label the
  same way, which goes to the camera with `label_event`.

Aircraft positions are kept only for this matching (at most 15 minutes on the hub, 10 on
the camera); the label's `detail` is all that stays.

**Live image.** The hub draws each aircraft above 0° altitude onto the newest live frame
with the lens (§4.8, as for constellations) at the frame's capture time, interpolated
between kept positions, or extrapolated from `track_deg` and `speed_kn` for at most 30 s
past the newest one (aircraft without them, or older, are not drawn): a marker, the
call sign and the altitude, with a short trail of its last minute. A switch "Flugzeuge"
next to "Sternbilder" shows them, remembered per browser. On the public page only with
the section `aircraft` (§6.8).

### 7.9 Satellites

Roadmap H6 (Benjamin, 2026-10-13): the hub names satellites among the meteor events, as it
names aircraft (§7.8); the camera needs no orbital data and no new dependency.

The hub loads the orbital elements (GP data) of CelesTrak's groups `active` and `visual`
once a day. For every meteor event that has both track ends (§6.4; they need an orientation
and a trusted clock), no label set by the owner and is not imported, it propagates every
satellite (SGP4) over `start .. end` in steps of at most 1 s, and keeps those above 5° and
in sunlight (the earth's shadow as a cylinder) for part of that time. A satellite is the
streak when both track ends lie within 1° of its apparent path and the streak is not longer
than that path plus 2°. If an aircraft (§7.8) and a satellite both match, the nearer one
counts. A match sets the automatic label `false_positive`/`satellite` with `detail` the
satellite's name and NORAD number (`"STARLINK-1234 44238"`; the ISS and Tiangong as
`"ISS"` and `"Tiangong"`), which goes to the camera with `label_event`. Events are checked
when they arrive, also after an outage, since orbits can be computed for any time.

## 8. Updates

Signed update packages (agent and profiles), installed by the device itself into an
A/B slot; the previous version is restored automatically if the new one does not
report healthy within a timeout.

- **Channel:** a JSON manifest `{channel, version, released_at, bundle: {url, sha256,
  size}}` (`allskyhub_protocol.updates`) at a fixed URL, with a detached Ed25519
  signature over its bytes at the same URL + `.sig`. The camera trusts the release keys
  in `/etc/allskyhub-agent/update-keys/`, which only an image brings, as changed by the key
  list.
- **Key revocation:** `keys.json` next to the manifest (+ `.sig`), signed by an emergency
  key from `update-keys/emergency/` (these sign nothing else): `{serial, issued_at,
  revoked, added}` (`allskyhub_protocol.updates.KeyList`), `revoked` the SHA-256 of each
  revoked release key's DER, `added` new release keys (base64 DER). Each list is complete
  and replaces the one before; the updater fetches it before the manifest and keeps the
  newest in its state folder, taking only a higher `serial`, so an older list, a missing
  one or one signed otherwise changes nothing. Release keys are the image's plus the added,
  minus the revoked. A leaked main key is revoked and replaced without a new image
  (Benjamin, 2026-10-13); a leaked emergency key needs a new image.
- **Bundle:** `<version>/` with the agent's venv, unpacked into
  `/opt/allskyhub-agent/releases/`; `current` points to the running one and is
  switched with one rename.
- **Updater:** a root systemd timer (10 min after boot, then hourly). Only newer
  versions, only with a valid signature and checksum, only while the agent reports day
  mode. Healthy means the local `/api/health` (§7; open without a login, only `version`,
  `frames` and the newest frame's `mode` and `captured_at`) reports the new `version`, and
  `frames` > 0 if the old one captured, within 5 min; else it switches back and skips
  that version. A power cut between switch and check is finished on the next run.
- **Asked for in the local UI** (roadmap L14): the agent writes `now` or `rollback` into
  `/var/lib/allskyhub-agent/update-request`; `allskyhub-updater.path` starts the updater,
  which reads and deletes it. `now` checks at once and installs also at night;
  `rollback` switches to the previous installed release with the same health check and
  marks the current one as not to be installed again by itself.
- **System updates:** Debian and Raspberry Pi OS security updates install by themselves
  (unattended-upgrades, no automatic reboot by apt). When they need a reboot
  (`/run/reboot-required`, a new kernel), the agent reboots once it is day: sun at least
  10° up, local time 10:00 to 16:00, no product being built, no update installing, at
  most once a day (Benjamin, 2026-10-13); until then `status.reboot_required` is true and
  the local UI and the hub show "Neustart für Sicherheitsupdates ausstehend".
- **Firewall:** the image's `nftables.conf` drops incoming connections except replies,
  ICMP, DHCP for the camera itself, the local web UI (8080, 8443), mDNS, SSH (off unless the
  owner turned it on) and, from the setup network `10.42.0.0/24`, DHCP and DNS (§7.1). It
  replaces only its own tables, never NetworkManager's. The agent's polkit right covers only
  what setup mode needs: activating connections, Wi-Fi scan and sharing, and adding
  connections; not the hostname, DNS, radio switches, sleep or checkpoints.
- Details and key handling: `docs/updates.md`.

### 8.1 Watchdog

The camera keeps itself working without anyone at it (roadmap L17). Capture never
depends on the hub (rule 1), so nothing here reacts to the hub being unreachable.

- **Agent:** runs under a systemd watchdog (`WatchdogSec=5min`) and pings it only while
  frames keep coming (a new frame within 10 minutes, or within 20 minutes of starting while
  it looks for the camera). A hung capture is restarted by systemd.
- **Watchdog check:** `allskyhub-watchdog.timer` runs `allskyhub-agent watchdog` as root
  every 5 minutes. It reads `GET /api/health` (§7) and escalates while the frame count does
  not rise: after 20 minutes restart the agent, after 35 minutes (and 15 after that restart)
  reset the ZWO camera on USB (`authorized` 0 → 1) and restart, after 60 minutes reboot, at
  most once every 6 hours. Once frames come again the ladder starts anew. If the local
  network (the default gateway answers a ping, or DNS resolves) has been unreachable for 3
  hours on a camera that had a network before, it reboots, with the same 6-hour limit. It
  does nothing while the updater switches versions (§8). State in
  `/var/lib/allskyhub-watchdog/state.json`.
- **Hardware:** systemd drives the Raspberry Pi's hardware watchdog
  (`RuntimeWatchdogSec=15s`), so a hung system resets itself.

## 9. Milestones

- **M0 – core (this repository's start):** protocol models, agent core (sun, day/night,
  auto exposure, storage), simulated camera, CLI.
- **M1 – real cameras:** ZWO (ASI SDK) and libcamera adapters, profiles, local web UI
  with focus helper.
- **M2 – hub:** pairing, live image, latest image page, device status.
- **M3 – app:** Flutter app (pairing, live view, settings, push), Codemagic builds.
- **M4 – products and detections:** timelapse, keogram, startrails, detection modules.
- **M5 – image and updates:** Pi image build (`docs/image.md`), signed A/B updates.
