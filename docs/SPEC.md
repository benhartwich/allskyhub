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
by days (default 14) and, from M1, by free disk space (keep at least 10 %).

## 5. Products (M4)

Timelapse, keogram and startrails per night, plus detections (meteor, lightning,
aurora, noctilucent clouds, sky quality, cloud cover). Detections are emitted as
protocol events (§6.4).

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

1. The app connects to the camera's setup hotspot (or finds it on the local network) and
   sends Wi-Fi credentials and the hub URL.
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
  (§4.4).
- `frame`: frame metadata (§4.4); the image itself goes over HTTPS when the hub asks
  for it (§6.5, `upload_frame`).
- `event`: a detection (§6.4).

### 6.4 Events

`{kind, start, end, confidence, image?, data}` with `kind` one of `meteor`,
`lightning`, `aurora`, `nlc`, `satellite`, `clouds`, `sky_quality`.

### 6.5 Hub → device

`command`: `set_settings`, `focus_mode` (`{on: bool}`, §7), `restart`, `update`,
`upload_frame`. Each command is acknowledged with `ack` or `error` (`code` one of
`not_found`, `invalid_args`, `unsupported`, `failed`).

**`upload_frame`** `{night_id, name, variant}` with `variant` `full` (default) or
`thumb`: the device uploads that image with
`PUT /device/v1/frames/{night_id}/{name}?variant=<variant>` (`image/jpeg`) and then
answers `ack`. If the frame no longer exists (retention, §4.5) it answers `error` with
`not_found`. The hub decides what to fetch and how often, for example the latest image
at most every few minutes, thumbnails for the app's gallery, and every frame in full
while someone watches the live view. The device needs no upload policy of its own.

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

Errors are HTTP status codes with `{"detail": "..."}`: 400 invalid body, 401 bad
signature, nonce or token, 403 device not paired (token), 404 unknown upload (the hub
did not ask for this frame), 413 image too large, 429 rate limited (with
`Retry-After`).

### 6.7 Offline behaviour

The device does not queue uploads or WebSocket messages while the hub is unreachable;
capture and storage go on (§1, goal 4). After reconnecting it sends `hello` and a fresh
`status`, and the hub requests whatever it wants with `upload_frame`.

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
expires_in, connected}`, where `pairing_code` is the raw 6-character code (no hyphen)
or `null` once paired or before the first registration, `expires_in` counts down in
seconds, and `connected` says whether the WebSocket to the hub is open.

Until pairing can restrict it, the local UI has no authentication and is meant for the
local network only.

## 8. Updates

Signed update packages (agent and profiles), installed by the device itself into an
A/B slot; the previous version is restored automatically if the new one does not
report healthy within a timeout.

## 9. Milestones

- **M0 – core (this repository's start):** protocol models, agent core (sun, day/night,
  auto exposure, storage), simulated camera, CLI.
- **M1 – real cameras:** ZWO (ASI SDK) and libcamera adapters, profiles, local web UI
  with focus helper.
- **M2 – hub:** pairing, live image, latest image page, device status.
- **M3 – app:** Flutter app (pairing, live view, settings, push), Codemagic builds.
- **M4 – products and detections:** timelapse, keogram, startrails, detection modules.
- **M5 – image and updates:** Pi image build, signed A/B updates.
