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

The setup Wi-Fi is protected by the camera's **setup key** (SPEC §7.1), 12 characters like
`ABCD-EFGH-JKMN` in `allskyhub-setup-key.txt` on the boot partition. Write your own there
before the first boot (letters and digits of `ABCDEFGHJKMNPQRSTUVWXYZ23456789`), or let
the camera make one on its first boot and read it from the card afterwards. The app asks
for it, or scans it as a Wi-Fi QR code. The file stays on the card; a new key takes
effect at the next boot.

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
| `allskyhub-wifi-country.path` | applies the Wi-Fi country the agent got from the app (root helper) |
| `nftables` | firewall (SPEC §8): only the local UI, mDNS, DHCP, SSH and setup mode come in; in setup mode `http://10.42.0.1/` reaches port 8080 |

The agent's state is in `/var/lib/allskyhub-agent`: device key, settings, images.

## Not yet in the image

- Signed updates with rollback (SPEC §8).
