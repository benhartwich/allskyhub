# Betrieb des Hubs (Debian 13)

So läuft der Hub auf allskyhub.org; für einen eigenen Hub die Domain ersetzen.

| | |
|---|---|
| Code | `/opt/allskyhub-server` (Git-Checkout, gehört root) |
| Python-Umgebung | `/opt/allskyhub-server/.venv` (`uv sync --frozen --no-dev --package allskyhub-server`) |
| Daten (Bilder) | `/var/lib/allskyhub-server/images/<kamera>/<nacht>/{full,thumb}/` (Benutzer `allskyhub-server`); Vollbilder 7 Tage, Vorschaubilder 30 Tage (`ALLSKYHUB_SERVER_KEEP_FULL_DAYS`, `_KEEP_THUMB_DAYS`; bei Änderung die Datenschutzerklärung anpassen) |
| Konfiguration | `/etc/allskyhub-server/allskyhub-server.env` (root:allskyhub-server, 0640), Vorlage `deploy/allskyhub-server.env.example` |
| Datenbank | PostgreSQL 17 aus Debian, Rolle `allskyhub_server`, Datenbank `allskyhub` |
| Dienst | `allskyhub-server.socket` + `.service` (`deploy/systemd/`), uvicorn auf `/run/allskyhub-server/api.sock` |
| Webserver | nginx, `deploy/nginx/allskyhub.conf` → `/etc/nginx/sites-available/allskyhub.conf` |
| Logs | `journalctl -u allskyhub-server`, `/var/log/nginx/allskyhub/` (ohne Query-Strings) |

Der Hub läuft mit **einem** Worker: offene Geräte-WebSockets liegen im Prozess.

## Ersteinrichtung

```bash
useradd --system --home-dir /var/lib/allskyhub-server --shell /usr/sbin/nologin allskyhub-server
install -d -o allskyhub-server -g allskyhub-server -m 0750 /var/lib/allskyhub-server /var/lib/allskyhub-server/tmp
install -d -o root -g allskyhub-server -m 0750 /etc/allskyhub-server
install -d -m 0755 /var/log/nginx/allskyhub
git clone https://github.com/benhartwich/allskyhub.git /opt/allskyhub-server
cd /opt/allskyhub-server && uv sync --frozen --no-dev --package allskyhub-server

sudo -u postgres psql -c "CREATE ROLE allskyhub_server LOGIN PASSWORD '…'"
sudo -u postgres createdb -O allskyhub_server allskyhub
# Env-Datei aus deploy/allskyhub-server.env.example anlegen, Passwort eintragen.

sudo -u allskyhub-server env $(xargs < /etc/allskyhub-server/allskyhub-server.env) \
  .venv/bin/allskyhub-server migrate
cp deploy/systemd/allskyhub-server.* /etc/systemd/system/
cp deploy/logrotate/allskyhub-nginx /etc/logrotate.d/   # nginx-Logs 14 Tage (Datenschutzerklärung)
systemctl daemon-reload && systemctl enable --now allskyhub-server.socket
```

## TLS

Solange es kein Zertifikat gibt, läuft `deploy/nginx/allskyhub-http.conf` (nur Port 80, mit
ACME-Pfad). Zertifikat für beide Namen über den Webroot:

```bash
certbot certonly --webroot -w /var/www/html -d allskyhub.org -d www.allskyhub.org
cp deploy/nginx/allskyhub.conf /etc/nginx/sites-available/allskyhub.conf
nginx -t && systemctl reload nginx
```

Danach leitet `www` und HTTP auf `https://allskyhub.org` um. Ohne HTTPS funktioniert die
Anmeldung nicht (sichere Cookies, Origin-Prüfung gegen `https://allskyhub.org`).

## Aktualisieren

```bash
cd /opt/allskyhub-server && git pull --ff-only
uv sync --frozen --no-dev --package allskyhub-server
sudo -u allskyhub-server env $(xargs < /etc/allskyhub-server/allskyhub-server.env) \
  .venv/bin/allskyhub-server migrate
systemctl restart allskyhub-server.service
curl -s https://allskyhub.org/healthz
```

Geräte verbinden sich nach dem Neustart von selbst wieder (SPEC §6.1).

## Konten (nur auf Einladung)

Es gibt keine offene Registrierung. Alle Befehle als Dienstbenutzer mit der Env-Datei:

```bash
cd /opt/allskyhub-server
alias ash='sudo -u allskyhub-server env $(xargs < /etc/allskyhub-server/allskyhub-server.env) .venv/bin/allskyhub-server'
ash invite name@example.org            # gibt den Einladungslink aus (7 Tage, einmal verwendbar)
ash users                              # Konten und Anzahl ihrer Kameras
ash set-password name@example.org      # neues Passwort, beendet alle Sitzungen
ash delete-user name@example.org --yes # Konto löschen, Kameras entkoppeln, Bilder löschen
```

Den Link schickst du selbst weiter (der Hub versendet keine E-Mails). Eine neue Einladung an
dieselbe Adresse macht die vorige ungültig. Abgelaufene Einladungen, Sitzungen und Tokens räumt
der Hub alle 6 Stunden auf.
