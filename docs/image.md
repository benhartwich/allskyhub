# The allskyhub image (Raspberry Pi 4 and 5)

The image is Raspberry Pi OS Lite (arm64, Trixie) with the camera agent, the ZWO camera SDK
and everything setup mode needs (SPEC §7.1–7.3). It is built by GitHub Actions
(`.github/workflows/image.yml`) and kept as a workflow artifact for 14 days.

## Flash and start

1. Download `allskyhub-image` from the newest successful **image** workflow run and unpack
   the zip.
2. Write `allskyhub-<version>.img.xz` to a microSD card with Raspberry Pi Imager
   ("Use custom"), without OS customisation. The camera does not need a login user.
3. Optional: put a setup file on the card's boot partition (see below).
4. Insert the card, connect the camera, power on.

Without a setup file and without Ethernet the camera opens the Wi-Fi `allskyhub-XXXX` after
the first boot. The app sets it up from there: Wi-Fi, location, time zone and pairing.

## Setup file

`allskyhub-setup.json` on the boot partition (SPEC §7.3) sets the camera up without the
app, for example:

```json
{"allskyhub_setup": 1,
 "wifi": {"ssid": "Home", "password": "secret-password"}, "wifi_country": "AT",
 "location": {"latitude": 48.14, "longitude": 14.39}, "timezone": "Europe/Vienna",
 "camera": "auto"}
```

To test an image on a Raspberry Pi **without a camera**, set `"camera": "sim"`. The camera
then works with a simulated sky in real time: capture, night products, local web UI,
pairing and the hub all behave as with a real camera.

## What runs on the camera

| Unit | Does |
|---|---|
| `allskyhub-firstboot` | once: host name `allskyhub`, Wi-Fi country so the radio is on |
| `allskyhub-setupfile` | hands the setup file to the agent and removes it from the boot partition |
| `allskyhub-agent` | the camera agent (user `allskyhub`), web UI on port 8080 |
| `nftables` | in setup mode `http://10.42.0.1/` reaches port 8080 |

The agent's state is in `/var/lib/allskyhub-agent`: device key, settings, images.

## Not yet in the image

- Signed updates with rollback (SPEC §8).
- Setting the Wi-Fi radio country from the app's `country`. The agent runs without root;
  first boot sets `AT` until this is done.
