# SharedSky: Kamera verbinden (`connect/v1`)

Mit dieser Schnittstelle kann jede Allsky-Kamera bei SharedSky mitmessen, egal mit welcher Software sie läuft: gekaufte Programme, eigene Skripte oder Bastelprojekte. Die Kamera schickt über HTTPS an den Hub. MQTT, FTP und Portfreigaben braucht es nicht. Den Vertrag dazu beschreibt `docs/SPEC.md` §10. Diese Seite ist die Kurzfassung für Entwicklerinnen und Entwickler.

Für **indi-allsky** reicht die Konfiguration, siehe ganz unten. Für **Allsky** kommt ein eigenes Modul.

## Anmelden

Lege ein kostenloses Konto an und wähle „Kamera verbinden“. Danach zeigt dir der Hub einmal zwei Werte:

- die **Station-ID**, 26 Zeichen, zum Beispiel `01JC3V8Q2Z7K4M9X5T6R2W8N1P`,
- das **Geheimnis**, 64 Hex-Zeichen. Es wird nur dieses eine Mal angezeigt. Ein neues kannst du jederzeit erzeugen, das alte gilt dann sofort nicht mehr.

Jede Anfrage trägt den Header

```
Authorization: Bearer <Station-ID>:<Geheimnis>
```

Die Adresse ist `https://allskyhub.org/api/connect/v1`. Alle Zeiten sind ISO 8601 in UTC, zum Beispiel `2026-11-05T21:40:12Z`.

## Die drei Aufrufe

### 1. Station melden: `PUT /station`

Schick das beim Start und wenn sich etwas ändert:

```json
{
  "name": "Sternwarte Musterberg",
  "latitude": 48.3,
  "longitude": 14.29,
  "elevation_m": 300,
  "software": "MySky",
  "software_version": "1.2",
  "lens": {"focal_mm": 1.55, "f_ratio": 2.0, "projection": "equidistant"},
  "width": 3096,
  "height": 2080,
  "clock_synced": true
}
```

- `width` × `height` ist die Größe des vollen Bildes, so wie die Kamera es speichert, nach einem eventuellen Zuschnitt. Alle Pixelangaben beziehen sich auf dieses Bild.
- Ändert sich die Größe, richtet der Hub die Kamera neu an den Sternen aus.
- `clock_synced` heißt: Die Uhr läuft per NTP oder GPS. Der Hub prüft das zusätzlich an jedem Bild.

Antwort: `{"orientation": null, "want_full_frame": false}`

### 2. Bild schicken: `POST /frames`

Etwa einmal pro Minute. Multipart mit zwei Teilen:

- `metadata`: JSON `{"time", "exposure_s", "gain", "night", "clock_synced", "stars", "sky_brightness"}`. `time` ist die Mitte der Belichtung.
- `image`: JPEG, normalerweise ein Vorschaubild mit höchstens 640 px an der langen Seite und höchstens 300 kB. Nur wenn die letzte Antwort `"want_full_frame": true` enthielt, kommt das volle Bild, höchstens 8 MB.

Antwort: `{"want_full_frame": false}`

### 3. Meteor melden: `POST /events`

Multipart:

- `metadata`: JSON
  ```json
  {
    "kind": "meteor",
    "start": "2026-11-05T21:40:00Z",
    "end": "2026-11-05T21:40:30Z",
    "peak": 0.8,
    "track": [[812, 431], [990, 502], [1140, 566]],
    "clock_synced": true
  }
  ```
  `track` sind 2 bis 32 Punkte entlang der Spur, in Pixeln des vollen Bildes. `peak` ist die Helligkeit von 0 bis 1 und optional: Ohne `peak` misst der Hub sie selbst.
- `image`: das volle Bild mit dem Meteor, genau `width` × `height`.

Antwort: `{"id": "meteor-20261105T214000Z"}`

## Testmodus

Unter `/api/connect/v1/test/station`, `/test/frames` und `/test/events` prüft der Hub jede Anfrage genauso, speichert aber nichts. Er antwortet, was er verstanden hat:

```json
{"ok": true, "understood": {...}}
```

## Grenzen und Fehler

- **Grenzen:** 120 Anfragen pro Minute und Station. Gespeichert wird höchstens ein Bild pro Minute. Pro Nacht nimmt der Hub höchstens 3000 Meteore an, pro Station höchstens 2 GB. Darüber fallen die ältesten vollen Bilder zuerst weg.
- **Fehler** kommen als `{"error": "<code>"}`:

| Status | `error` | Bedeutung |
|---|---|---|
| 400 | `bad_request` | ein Feld fehlt oder liegt außerhalb des Bereichs, dazu `field` |
| 400 | `bad_image` | kein lesbares JPEG |
| 400 | `wrong_size` | das volle Bild hat nicht `width` × `height` |
| 401 | `unauthorized` | unbekannte Station oder falsches Geheimnis |
| 413 | `too_large` | Bild zu groß |
| 429 | `too_often` / `night_full` | zu viele Anfragen oder zu viele Meteore in dieser Nacht, mit `Retry-After` |

## Beispiel mit curl

```bash
AUTH="Authorization: Bearer $STATION_ID:$SECRET"
curl -X PUT https://allskyhub.org/api/connect/v1/station -H "$AUTH" \
     -H 'Content-Type: application/json' -d @station.json
curl https://allskyhub.org/api/connect/v1/frames -H "$AUTH" \
     -F 'metadata={"time":"2026-11-05T21:40:15Z","exposure_s":30,"night":true,"clock_synced":true};type=application/json' \
     -F image=@thumb.jpg
```

## Beispiel in Python

```python
import json

import requests

BASE = "https://allskyhub.org/api/connect/v1"
AUTH = {"Authorization": f"Bearer {STATION_ID}:{SECRET}"}

requests.put(f"{BASE}/station", json=station, headers=AUTH, timeout=30).raise_for_status()

meta = {"time": "2026-11-05T21:40:15Z", "exposure_s": 30, "night": True, "clock_synced": True}
with open("thumb.jpg", "rb") as image:
    r = requests.post(
        f"{BASE}/frames",
        headers=AUTH,
        files={"metadata": (None, json.dumps(meta), "application/json"), "image": image},
        timeout=30,
    )
print(r.json())  # {"want_full_frame": false}
```

## indi-allsky

indi-allsky braucht keine Installation. In der Konfiguration unter **SyncAPI**:

1. SyncAPI einschalten.
2. **Base URL:** `https://allskyhub.org/indi-allsky`
3. **Username:** die Station-ID
4. **API Key:** das Geheimnis
5. Bilder synchronisieren: jedes Bild.

indi-allsky schickt dann mit jedem Bild Standort, Objektiv, Belichtung, Sternzahl und die Zahl erkannter Meteorspuren. Der Hub behält davon ein Vorschaubild. Volle Bilder behält er nur, wenn indi-allsky einen Meteor gesehen hat.

## Was mit den Daten passiert

- **Bilder** bleiben deine. Der Hub zeigt sie nur dort, wo du es erlaubst, und immer mit dem Namen der Station.
- **Messwerte** teilt der Hub unter CC BY 4.0 mit dem Namen der Station: Zeiten, Spuren, Bahnen, Raten und Himmelshelligkeit. Nur so kann er sie an wissenschaftliche Partner weitergeben.
- **Standort:** Der genaue Ort dient nur der Rechnung. Öffentlich steht er immer gerundet.
- **Austritt:** Entfernst du die Station, löscht der Hub sofort ihre Bilder, den genauen Ort und das Geheimnis.
