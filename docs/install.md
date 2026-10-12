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
again on your home network and pairs it with your account. You type nothing but the Wi-Fi
password.

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
apt install postgresql nginx certbot git curl
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
alias ash='sudo -u allskyhub-server env $(xargs < /etc/allskyhub-server/allskyhub-server.env) /opt/allskyhub-server/.venv/bin/allskyhub-server'
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
