# Installation

allskyhub has three parts: the **camera** (a Raspberry Pi with the allskyhub image), the
**hub** (the server the camera talks to, [allskyhub.org](https://allskyhub.org) or your own)
and the **app**. This guide goes through all of them.

> **Status:** allskyhub is in testing. Accounts on allskyhub.org are by invitation, the app
> is not in the stores yet, and images are built by CI rather than published as releases.

## 1. What you need

| | |
|---|---|
| Computer | Raspberry Pi 5 or 4 (2 GB RAM or more) |
| Camera | ZWO ASI678MC over USB 3 (tested). Raspberry Pi HQ camera: profile exists, not yet tested |
| Lens | an all-sky fisheye, e.g. 1.55–2.5 mm (M12 or CS mount, matching the camera) |
| Storage | microSD card, 32 GB or more (A2, endurance cards last longer) |
| Power | the official Raspberry Pi power supply |
| Network | Wi-Fi or Ethernet with internet access. No port forwarding, no fixed IP: the camera only connects outward |
| Housing | weatherproof, with a clear dome |

## 2. Set up the camera

### 2.1 Write the image

1. Download the image: on GitHub open **Actions → image**, take the newest successful run
   and download the artifact `allskyhub-image`. Unpack the zip.
2. Write `allskyhub-<version>.img.xz` to the microSD card with
   [Raspberry Pi Imager](https://www.raspberrypi.com/software/): *Choose OS → Use custom*.
   Skip the OS customisation; the camera needs no login user.
3. Optional, for setup without the app: put an `allskyhub-setup.json` on the card's boot
   partition (section 2.3).
4. Put the card into the Pi, connect the camera, switch on.

Details about the image, its services and the setup file: [image.md](image.md).

### 2.2 Set up with the app (normal way)

Without a setup file and without Ethernet the camera opens a Wi-Fi called
`allskyhub-XXXX` after the first boot. In the app tap **Neue Kamera einrichten**: the app
connects to that Wi-Fi, gives the camera your home network, location and time zone, finds it
again on your home network and pairs it with your account. The setup Wi-Fi is protected:
the app asks for the camera's setup key, from the QR label of a bought camera or from
`allskyhub-setup-key.txt` on the SD card (docs/image.md). Then you type nothing but your
Wi-Fi password.

### 2.3 Set up without the app

Put this file as `allskyhub-setup.json` on the boot partition before the first start (all keys
except `allskyhub_setup` are optional):

```json
{"allskyhub_setup": 1,
 "wifi": {"ssid": "Home", "password": "secret-password"}, "wifi_country": "AT",
 "hub_url": "https://allskyhub.org",
 "location": {"latitude": 48.14, "longitude": 14.39}, "timezone": "Europe/Vienna",
 "camera": "auto"}
```

With Ethernet you can leave out `wifi`. The camera reads the file once and deletes it.

Then pair it in the browser:

1. Open the camera's local page at `http://<camera-ip>:8080` (your router's list of
   devices shows the address; the host name is `allskyhub`). It shows a pairing code such
   as `ABC-DEF`, valid 15 minutes.
2. Sign in on the hub, choose **Kamera koppeln** and enter the code.

### 2.4 After pairing

- The hub shows the live picture, status, gallery, night products (keogram, startrails,
  timelapse) and detections (meteors, aurora, lightning, noctilucent clouds).
- Settings (location, time zone, camera, pause between frames) can be changed on the hub
  and in the app while the camera is online.
- A **public page** can be switched on per camera and designed under *Seite gestalten*.
- The camera keeps capturing and storing when the internet is gone, and catches up when it
  is back.

To test an image without a camera, use `"camera": "sim"`: everything works with a
simulated sky.

### 2.5 Local page after pairing

Before pairing, the local page at `http://<camera-ip>:8080` is open on your network. Once
the camera is paired, it asks for a login ([SPEC §7.4](SPEC.md)):

- **Mit allskyhub anmelden:** you sign in on the hub, and it sends you back logged in for
  30 days.
- **Offline code:** for when the internet is down. You create it on the hub under the
  camera's settings.

### 2.6 Install on an existing Raspberry Pi, next to Allsky

If a Pi already runs [Allsky](https://github.com/AllskyTeam/allsky), the agent can run on it
as well, without the image. Only one program can use the camera, so you switch between
them. The image's Wi-Fi handling, setup mode, updater and firewall are left out: the Pi
keeps its own network setup.

1. **User, data directory and ZWO SDK.** Allsky's own copy of the SDK works:

   ```bash
   sudo useradd --system --home-dir /var/lib/allskyhub-agent --no-create-home \
     --shell /usr/sbin/nologin --groups video allskyhub
   sudo install -d -o allskyhub -g allskyhub -m 0750 /var/lib/allskyhub-agent /var/lib/allskyhub-agent/data
   sudo install -d /opt/allskyhub-agent/lib
   sudo install -m 0755 ~/allsky/server/assets/asi_sdk/lib/armv8/libASICamera2.so.1.39 /opt/allskyhub-agent/lib/
   sudo ln -sfn libASICamera2.so.1.39 /opt/allskyhub-agent/lib/libASICamera2.so
   ```

2. **Agent release.** Build it from a checkout with [uv](https://docs.astral.sh/uv/) into its
   own release directory. Updating works the same way: build a new directory, then point
   `current` at it.

   ```bash
   V=0.1.0-$(git -C ~/allskyhub rev-parse --short=7 HEAD)
   R=/opt/allskyhub-agent/releases/$V
   sudo install -d $R
   cd ~/allskyhub && sudo env UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=$R/.venv \
     sh -c "uv venv -q --python /usr/bin/python3 $R/.venv && uv sync -q --frozen --no-dev --no-editable --package allskyhub-agent"
   sudo ln -sfn releases/$V /opt/allskyhub-agent/current
   ```

3. **Settings file.** Without network management (`--network none`) the agent keeps its
   settings only with `--settings`. Then location, time zone, camera and the rest can be
   changed in the local UI, on the hub and in the app. Put this into
   `/etc/allskyhub-agent/allskyhub-agent.env`:

   ```bash
   ALLSKYHUB_AGENT_ARGS="--asi-sdk /opt/allskyhub-agent/lib/libASICamera2.so run --data /var/lib/allskyhub-agent/data --settings /var/lib/allskyhub-agent/settings.json --hub https://allskyhub.org --http 0.0.0.0:8080"
   ```

   and start with a settings file (owned by `allskyhub`):

   ```json
   {"latitude": 48.14, "longitude": 14.39, "timezone": "Europe/Vienna", "camera": "zwo-asi678mc"}
   ```

   `--lat`, `--lon`, `--tz` and `--profile` on the command line would override the file,
   so leave them out.

4. **Service, not enabled.** Copy `image/files/etc/systemd/system/allskyhub-agent.service`
   to `/etc/systemd/system/` and add a drop-in
   `/etc/systemd/system/allskyhub-agent.service.d/allsky-switch.conf`:

   ```ini
   [Unit]
   Conflicts=allsky.service
   After=allsky.service
   ```

   `Conflicts=` stops Allsky when the agent starts and the other way round. `After=` makes
   systemd finish the stop before the start; without it both programs open the camera at
   the same time. Leave the agent disabled, so the Pi starts with Allsky after a reboot.

5. **Switch.** `sudo systemctl start allskyhub-agent` (Allsky stops) or
   `sudo systemctl start allsky` (the agent stops). A small script for this,
   `allsky-switch hub|allsky|status`, saves typing. Note that Allsky's udev rule starts Allsky
   whenever the camera is plugged in again.

6. **Pair** as in section 2.3: open `http://<pi-ip>:8080` and enter the code on the hub.

7. **Optional: the local page under your Allsky domain.** If lighttpd already serves the
   Allsky WebUI with a certificate, it can forward the domain to the agent while the agent
   runs. `/etc/lighttpd/conf-available/allskyhub-proxy.conf`:

   ```
   server.modules += ( "mod_proxy" )
   $HTTP["host"] == "cam.example.org" {
       $HTTP["url"] !~ "^/\.well-known/" {
           proxy.server = ( "" => ( ( "host" => "127.0.0.1", "port" => 8080 ) ) )
       }
   }
   ```

   Link it into `conf-enabled/` when the agent starts and remove it when it stops, for
   example with `ExecStartPost=+` and `ExecStopPost=+` in another drop-in that run a script
   and then `systemctl reload lighttpd`. `/.well-known/` stays with lighttpd so certificate
   renewal keeps working. lighttpd sends `X-Forwarded-Proto`, so the login cookie is
   marked `Secure` behind HTTPS.

### 2.7 Take over the pictures of an existing Allsky

On a Pi that ran Allsky, the agent can take over everything Allsky stored: every night's
images with their metadata (from Allsky's database where it has them, else from the file
name), keogram, star trails and timelapse, and the meteors of the meteor module. Images
are hard-linked when Allsky's folder is on the same disk, so this needs no extra space;
only the agent's thumbnails are new. Run it as root (hard links to another user's files
need it), first as a dry run:

```bash
sudo /opt/allskyhub-agent/current/.venv/bin/allskyhub-agent import-allsky \
  --from /home/allsky/allsky --data /var/lib/allskyhub-agent/data \
  --settings /var/lib/allskyhub-agent/settings.json --dry-run
```

Without `--dry-run` it imports; `--nights 20261011,20261012` limits it to some nights.
Running it again only adds what is new. If the agent has no location yet, it takes
Allsky's. The running agent then announces the imported nights to the hub, one per
second; the hub shows their products and meteors (marked as imported, no push
notifications).

### 2.8 Take over the pictures of an existing indi-allsky

The same works for [indi-allsky](https://github.com/aaronwmorris/indi-allsky): the agent
reads its database (read-only, default `/var/lib/indi-allsky/indi-allsky.sqlite`) and
takes over every JPEG image with its metadata (time, exposure, gain, sensor temperature,
day or night), the keograms, star trails and H.264 timelapses (night and day), and its
location if the agent has none. indi-allsky's line detections, its own SQM value, PNG/TIFF
images and WebM timelapses do not come over. Hard links, dry run and resuming work as for
Allsky:

```bash
sudo /opt/allskyhub-agent/current/.venv/bin/allskyhub-agent import-indi-allsky \
  --data /var/lib/allskyhub-agent/data \
  --settings /var/lib/allskyhub-agent/settings.json --dry-run
```

`--db` and `--images` point to another database or image folder (default: the
`IMAGE_FOLDER` of its configuration, usually `/var/www/html/allsky/images`). If several
cameras have images, `--camera <id>` picks one; the dry run lists them.

## 3. An account on allskyhub.org

Accounts are by invitation for now. Ask the project for an invitation; you get a link to set
your password. Afterwards you can change the password and delete the account yourself
under **Konto**.

## 4. Run your own hub

The hub is a FastAPI app with PostgreSQL behind nginx. These steps are for Debian 13
(trixie); the operations guide for allskyhub.org with backups, monitoring and push is
[betrieb.md](betrieb.md) (German).

### 4.1 Packages and user

```bash
apt install postgresql nginx certbot git curl ffmpeg
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh

useradd --system --home-dir /var/lib/allskyhub-server --shell /usr/sbin/nologin allskyhub-server
install -d -o allskyhub-server -g allskyhub-server -m 0750 /var/lib/allskyhub-server /var/lib/allskyhub-server/tmp
install -d -o root -g allskyhub-server -m 0750 /etc/allskyhub-server
install -d -m 0755 /var/log/nginx/allskyhub
```

### 4.2 Code and database

```bash
git clone https://github.com/benhartwich/allskyhub.git /opt/allskyhub-server
cd /opt/allskyhub-server && uv sync --frozen --no-dev --package allskyhub-server

sudo -u postgres psql -c "CREATE ROLE allskyhub_server LOGIN PASSWORD 'a-long-random-password'"
sudo -u postgres createdb -O allskyhub_server allskyhub
```

### 4.3 Configuration

```bash
cp deploy/allskyhub-server.env.example /etc/allskyhub-server/allskyhub-server.env
chown root:allskyhub-server /etc/allskyhub-server/allskyhub-server.env
chmod 0640 /etc/allskyhub-server/allskyhub-server.env
```

Edit it: the database password and `ALLSKYHUB_SERVER_BASE_URL` (your domain, with
`https://`). All settings have the prefix `ALLSKYHUB_SERVER_`; see
`server/src/allskyhub_server/settings.py`.

```bash
alias ash='sudo -u allskyhub-server bash -c '\''set -a; . /etc/allskyhub-server/allskyhub-server.env; exec "$@"'\'' _ /opt/allskyhub-server/.venv/bin/allskyhub-server'
ash migrate
```

### 4.4 Service

```bash
cp deploy/systemd/allskyhub-server.* /etc/systemd/system/
cp deploy/logrotate/allskyhub-nginx /etc/logrotate.d/
systemctl daemon-reload && systemctl enable --now allskyhub-server.socket
```

The hub runs with one worker: the cameras' WebSockets live in that process.

### 4.5 nginx and TLS

Replace `allskyhub.org` with your domain in both files under `deploy/nginx/`.

```bash
cp deploy/nginx/allskyhub-http.conf /etc/nginx/sites-available/allskyhub.conf
ln -s /etc/nginx/sites-available/allskyhub.conf /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
certbot certonly --webroot -w /var/www/html -d example.org -d www.example.org
cp deploy/nginx/allskyhub.conf /etc/nginx/sites-available/allskyhub.conf
nginx -t && systemctl reload nginx
curl -s https://example.org/healthz
```

HTTPS is required: sign-in uses secure cookies and checks the origin.

### 4.6 First account

```bash
ash invite you@example.org   # prints a link, valid 7 days, single use
```

The hub sends no e-mail yet; pass the link on yourself. `ash users`, `ash set-password` and
`ash delete-user` manage accounts.

### 4.7 Point cameras at your hub

Set `"hub_url": "https://example.org"` in the setup file (section 2.3).

### 4.8 Update

```bash
cd /opt/allskyhub-server && git pull --ff-only
uv sync --frozen --no-dev --package allskyhub-server
ash migrate
systemctl restart allskyhub-server.service
```

Cameras reconnect by themselves.

## 5. Development

```bash
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run pyright
uvx --from 'reuse[charset-normalizer]' reuse lint
tools/dev-postgres.sh                      # PostgreSQL in Docker for the hub's tests
uv run allskyhub-agent --sim --lat 48.14 --lon 14.39 run --frames 20 --data /tmp/ash
```

The protocol between camera and hub is specified in [SPEC.md](SPEC.md).
