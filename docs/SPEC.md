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

### 4.2 Day and night

The mode follows the sun's elevation at the camera's location:

- `night` when the elevation is below `night_angle` (default −6°),
- `day` when it is above `night_angle + hysteresis` (default hysteresis 1°),
- otherwise the previous mode is kept.

The sun position uses the NOAA approximation (accuracy better than 0.1° near the
horizon, more than enough here). Time comes from an injected clock.

### 4.3 Auto exposure

The controller aims for a target mean brightness (`target_mean`, 0..1, default day
0.35, night 0.20) measured inside the mask.

- It works in log space: `ratio = target / measured`, damped as
  `ratio ** damping` (default 0.7) and clamped to `[1/8, 8]` per frame, so a single odd
  frame cannot swing the exposure wildly.
- Exposure is changed first. Gain is raised only when the exposure is at its mode's
  maximum, and lowered first when the image is too bright.
- A measured mean of 0 is treated as `1/65536` so the controller can always recover.
- On a mode change the controller starts from that mode's last known good values.

### 4.4 Frame metadata

Every frame carries: capture time (UTC), mode, exposure (µs), gain, measured mean,
sun elevation, sensor temperature if available, and the profile id.

Timestamps come from the system clock, synchronized by NTP. A Pi has no real-time clock,
so after a boot without network the clock can be wrong: the agent reports
`time_trusted` (true once the clock is NTP-synchronized) in every `status` (§6.3), and
the hub treats frames from a device with `time_trusted: false` as possibly misdated.

### 4.5 Storage

Frames are stored as JPEG under `<data>/images/<night-id>/` with a thumbnail. The
*night id* is the date of the evening the night started: frames before local noon
belong to the previous day's id, so a whole night lands in one folder. Retention is
by days (default 14, after the night products) and by free disk space: every 10 minutes the oldest nights are removed until at least 10 % of the disk is free; the current night is never removed.

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

## 5. Products (M4)

### 5.1 Frame index

Every stored frame is appended to `<data>/images/<night-id>/frames.jsonl`, one JSON
object per line: the frame's metadata (§4.4, the `frame` message body). Products and
the hub's gallery read it instead of decoding every image. A missing or damaged line is
skipped.

### 5.2 Night products

At dawn, when the mode switches from night to day, the agent builds three products from
that night's frames (frames with mode `night` in the night id's index) in a background
worker; capture never waits for it.

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

### 5.3 Detections

Meteor, lightning, aurora, noctilucent clouds, sky quality and cloud cover are emitted
as protocol events (§6.4).

## 6. Device protocol v1 (draft)

### 6.1 Transport

The agent opens an outbound WebSocket (`wss://<hub>/device/v1/ws`, default hub
`allskyhub.org`) and uses HTTPS for registration, tokens and uploads (§6.6). Every
WebSocket message is an envelope `{v: 1, type, id, ts, body}`.

The WebSocket upgrade and every upload carry `Authorization: Bearer <access_token>`
(§6.2, step 5). The hub closes the WebSocket with:

| Code | Meaning | Agent does |
|---|---|---|
| 4401 | token expired or invalid | gets a new token, reconnects |
| 4403 | device is no longer paired | registers again (§6.2) |
| 4409 | a newer connection of the same device took over | does not reconnect on this socket |

The agent reconnects with exponential backoff (1 s doubling to 5 min, with jitter).

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

### 6.3 Device → hub

- `hello`: device id, profile, agent version, capabilities.
- `status`: mode, last exposure/gain/mean, temperatures, disk, uptime, `time_trusted`
  (§4.4), and `settings` (`{latitude, longitude, timezone, camera, day_delay_s,
  night_delay_s}`, location null until known) for the app's settings screen, and
  `sky` (null until the first frame is measured): `{at, night_id, cloud_cover, sqm_mag,
  stars}` measured on the newest frame. `at` is that frame's `captured_at` and
  `night_id` its night (§4.5); status repeats the last `sky` until a new frame is
  measured, so the hub keeps one sample per `at`. `cloud_cover` (0..1) comes from the
  stars visible in the upper sky at night (sun ≤ −12°), else null. `sqm_mag` (mag/arcsec², approximate until calibrated) is the
  sky background normalised by exposure and gain, only at astronomical night (sun ≤
  −18°), else null. `stars` is the number of stars detected at night, else null.
  `orientation` (null until solved, §4.8): `{north_deg, mirrored, solved_at, stars,
  rms_deg}`. `north_deg` (0..360) is the image direction of north at the horizon, 0 = up,
  clockwise, like `direction_deg` in events; `mirrored` true means east is
  counter-clockwise from north (the usual view of a camera looking up). The azimuth of
  an image direction d is (north_deg − d) mod 360 when mirrored, else
  (d − north_deg) mod 360; exact at the horizon, approximate above it if the camera
  leans.
  `update` (null until the updater ran, §8): `{state, version, at, code}` with `state`
  one of `up_to_date`, `downloading`, `waiting` (downloaded, waits for the day),
  `installing`, `installed`, `rolled_back`, `failed`; `version` the release it is about,
  `at` when that state was reached, `code` why it failed or rolled back (e.g.
  `unhealthy`, `checksum`, `bad_signature`, `no_space`).
- `frame`: frame metadata (§4.4); the image itself goes over HTTPS when the hub asks
  for it (§6.5, `upload_frame`).
- `event`: a detection (§6.4).
- `products`: the night products (§5.2) of one night, `{night_id, products: [{kind,
  name, content_type, size, thumbnail, duration_s}]}` (`size` in bytes; `duration_s`
  only for the timelapse, else null) with `kind` `keogram`, `startrails` or
  `timelapse`, `name` `keogram.jpg`, `startrails.jpg` or `timelapse.mp4`, and
  `content_type` `image/jpeg` or `video/mp4`. Sent when a night's products have been
  built, and once after every (re)connect for the newest night that has products. Only
  existing products are listed; the files themselves go over HTTPS when the hub asks for
  them (§6.5, `upload_product`).

### 6.4 Events

`{id, night_id, kind, start, end, confidence, has_image, data}` with `kind` one of
`meteor`, `lightning`, `aurora`, `nlc`, `satellite`, `clouds`, `sky_quality`.

- `id` is stable on the device: the kind without underscores and the UTC start, e.g.
  `meteor-20261008T214512Z`, with `-2`, `-3` … for further events of the same kind
  starting in the same second. A resent event has the same id; the hub upserts by it.
- `has_image`: the event has a picture, which the hub fetches with `upload_event` (§6.5).
- `data` is a flat object of numbers, strings, booleans and nulls. Per kind:

| kind | keys |
|---|---|
| `meteor` | `length_px` (int, track length), `peak` (0..1, peak brightness of the track), `frames` (int, frames it appears in), `direction_deg` (0..360 in the image, 0 = up, clockwise, or null), `shower` (name of an active shower, e.g. `Perseids`, or null) |
| `lightning` | `area_frac` (0..1, part of the sky that lit up), `peak` (0..1, mean brightening of the lit area), `storm_flashes` (int, flashes in the last 30 min including this one), `storm` (string, `storm-` + UTC start of the storm like an event id; the same for all flashes of a storm, a gap of more than 30 min starts a new one) |
| `aurora` | one event per episode (below): `peak_index` (0..100, % of the searched band that is aurora-green in the best frame; the band is the low sky (2–36°) within 70° of the pole's azimuth once the camera has an orientation (§4.8), else the whole low ring of the image circle), `green` (mean green over red of those pixels, 8 bit), `frames` (int, candidate frames so far), `direction_deg` (0..360 in the image, 0 = up, clockwise, of the green's centroid; not a compass bearing), `ongoing` (bool), `image_rev` |
| `nlc` | one episode (below), only with an orientation (§4.8), sun −16° to −9°, in the low sky (12–45°) toward the sun: `peak_index` (0..100, % of that band that is NLC-blue and structured, best frame), `blue` (mean blue over red of those pixels, 8 bit), `frames`, `direction_deg`, `ongoing`, `image_rev` |

`meteor`, `lightning`, `aurora` and `nlc` also carry `azimuth_deg` (0..360, compass, 0 = north,
90 = east) and `altitude_deg` (degrees above the horizon) of their position in the sky:
the meteor's midpoint, the centroid of the lit area, of the green or of the blue. The
agent computes them with the orientation (§4.8) valid when it saves the event, so they
stay right after a later re-solve; both are null while the camera has no orientation.

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

### 6.5 Hub → device

`command`: `set_settings`, `focus_mode` (`{on: bool}`, §7), `restart`, `update`,
`upload_frame`, `upload_product`, `upload_event`. Each command is acknowledged with `ack` or `error` (`code` one of
`not_found`, `invalid_args`, `unsupported`, `failed`).

**`set_settings`** with a partial object: `latitude` and `longitude` (both or neither),
`timezone` (IANA), `camera` (`auto`, `zwo-asi678mc`, `rpi-hq`, `sim`), `day_delay_s` and
`night_delay_s` (0..3600 s between frames). Only the given keys change; an unknown key or
an invalid value changes nothing and is answered with `error` `invalid_args` naming the
fields. On success the device stores the settings, answers `ack` and restarts its
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

### 6.6 Device HTTP endpoints

All bodies are JSON (models in `packages/protocol`, `allskyhub_protocol.device_api`)
except the image upload.

| Endpoint | Auth | Body → answer |
|---|---|---|
| `POST /device/v1/challenge` | none | `{device_id}` → `{nonce, expires_in}` |
| `POST /device/v1/register` | signature (`register`) | `{public_key, profile, agent_version, nonce, signature}` → `{device_id, paired, pairing_code?, expires_in?}` |
| `POST /device/v1/token` | signature (`token`) | `{device_id, nonce, signature}` → `{access_token, token_type: "bearer", expires_in}` |
| `GET /device/v1/ws` | bearer | WebSocket (§6.1) |
| `PUT /device/v1/frames/{night_id}/{name}?variant=` | bearer | `image/jpeg` → 204 |
| `PUT /device/v1/products/{night_id}/{name}?variant=` | bearer | `image/jpeg` or `video/mp4` → 204 |
| `PUT /device/v1/events/{night_id}/{event_id}?variant=` | bearer | `image/jpeg` → 204 |

Errors are HTTP status codes with `{"detail": "..."}`: 400 invalid body, 401 bad
signature, nonce or token, 403 device not paired (token), 404 unknown upload (the hub
did not ask for this frame), 413 image too large, 429 rate limited (with
`Retry-After`).

### 6.7 Offline behaviour

The device does not queue uploads or WebSocket messages while the hub is unreachable;
capture and storage go on (§1, goal 4). After reconnecting it sends `hello`, a fresh
`status`, the newest night's `products` and the current night's `event`s, and the hub
requests whatever it wants with `upload_frame`, `upload_product` and `upload_event`.

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

Until pairing can restrict it, the local UI has no authentication and is meant for the
local network only.

### 7.1 Setup mode

The camera opens its own Wi-Fi network for setup:

- **When:** on start without any configured Wi-Fi and without Ethernet, or when the
  configured Wi-Fi has been unreachable for 2 minutes and there is no Ethernet.
- **Network:** open Wi-Fi `allskyhub-XXXX`, where `XXXX` is the first four characters of
  the device id in upper case. The camera is `10.42.0.1`, hands out addresses by DHCP
  and answers every DNS query with `10.42.0.1` (captive portal). The local web UI is at
  `http://10.42.0.1:8080/`; port 80 redirects there.
- **End:** after the camera has joined a network, or after 15 minutes without a request.

Endpoints, only reachable from the setup network:

| Endpoint | Body → answer |
|---|---|
| `GET /api/setup` | as in §7, plus `setup_mode: true` and `last_error` |
| `GET /api/wifi/networks` | → `[{ssid, signal, secure}]`, strongest first, hidden networks left out |
| `POST /api/setup/network` | `{ssid, password?, country, hub_url?, latitude?, longitude?, timezone?}` → `202 {will_join: ssid}` |

- `country` is the ISO 3166 code for the radio rules (set by a root helper of the image before joining); `hub_url` overrides the configured
  hub (default `https://allskyhub.org`).
- `latitude` and `longitude` (degrees, both or neither) and `timezone` (IANA name, e.g.
  `Europe/Vienna`) come from the phone. The camera needs the location for day and night
  (§4.2) and the time zone for its night folders (§4.5); it only starts capturing once it
  has a location. They are stored with the camera's settings once it has joined.
- After answering `202` the camera leaves setup mode and joins the network. If that fails
  it opens the setup network again and `/api/setup` reports `last_error` as
  `wifi_auth`, `wifi_not_found` or `no_internet` (otherwise `null`).
- The Wi-Fi password is stored only in the system's network configuration, never in
  logs and never sent to the hub.

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

All keys except `allskyhub_setup` are optional. `camera` is `auto` (default: a ZWO
camera if one is connected, else a Raspberry Pi camera), `zwo-asi678mc`, `rpi-hq` or `sim`
(a simulated camera, for testing an image without a camera). The file is read once at
boot, applied (Wi-Fi as a NetworkManager connection, country, hub, location, time zone,
camera) and deleted. An unusable file is renamed to `allskyhub-setup.failed.json` and not tried
again.

## 8. Updates

Signed update packages (agent and profiles), installed by the device itself into an
A/B slot; the previous version is restored automatically if the new one does not
report healthy within a timeout.

- **Channel:** a JSON manifest `{channel, version, released_at, bundle: {url, sha256,
  size}}` (`allskyhub_protocol.updates`) at a fixed URL, with a detached Ed25519
  signature over its bytes at the same URL + `.sig`. The camera trusts the public keys
  in `/etc/allskyhub-agent/update-keys/`, which only an image brings.
- **Bundle:** `<version>/` with the agent's venv, unpacked into
  `/opt/allskyhub-agent/releases/`; `current` points to the running one and is
  switched with one rename.
- **Updater:** a root systemd timer (10 min after boot, then hourly). Only newer
  versions, only with a valid signature and checksum, only while the agent reports day
  mode. Healthy means the local `/api/status` (§7) reports the new `version`, and
  `frames` > 0 if the old one captured, within 5 min; else it switches back and skips
  that version. A power cut between switch and check is finished on the next run.
- Details and key handling: `docs/updates.md`.

## 9. Milestones

- **M0 – core (this repository's start):** protocol models, agent core (sun, day/night,
  auto exposure, storage), simulated camera, CLI.
- **M1 – real cameras:** ZWO (ASI SDK) and libcamera adapters, profiles, local web UI
  with focus helper.
- **M2 – hub:** pairing, live image, latest image page, device status.
- **M3 – app:** Flutter app (pairing, live view, settings, push), Codemagic builds.
- **M4 – products and detections:** timelapse, keogram, startrails, detection modules.
- **M5 – image and updates:** Pi image build (`docs/image.md`), signed A/B updates.
