# allskyhub – Specification (draft v0.1)

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
`allskyhub.org`) and uses HTTPS for
uploads. Every message is an envelope `{v: 1, type, id, ts, body}`.

### 6.2 Identity and pairing

Each device generates an Ed25519 key pair on first boot; its device id is derived from
the public key. Pairing:

1. The app connects to the camera's setup hotspot (or the local network) and sends Wi-Fi
   credentials and the hub URL.
2. The agent registers its public key with the hub and receives a short pairing code.
3. The app shows the code; the user confirms it in their hub account. The device is
   bound to the account.

### 6.3 Device → hub

- `hello`: device id, profile, agent version, capabilities.
- `status`: mode, last exposure/gain/mean, temperatures, disk, uptime.
- `frame`: frame metadata (§4.4); the image itself goes over HTTPS when the hub asks
  for it (live view, latest image, products).
- `event`: a detection (§6.4).

### 6.4 Events

`{kind, start, end, confidence, image?, data}` with `kind` one of `meteor`,
`lightning`, `aurora`, `nlc`, `satellite`, `clouds`, `sky_quality`.

### 6.5 Hub → device

`command`: `set_settings`, `focus_mode` (start/stop), `restart`, `update`. Each command
is acknowledged with `ack` or `error`.

## 7. Local web UI

A small mobile-first page served by the agent on the local network: live image, focus
helper (sharpness value), status. It also works without any hub.

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
