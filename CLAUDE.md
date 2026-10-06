# CLAUDE.md – Projekt allskyhub

**allskyhub** – Plug-and-play-Allsky-Kamera: Raspberry Pi mit kuratierter Kamera, fertiges Image, Kopplung per Handy, ausgehende Verbindung zum Hub (kein FTP, keine Portfreigabe), Flutter-App für Android und iOS. Open Source.

## Verbindlicher Vertrag

**`docs/SPEC.md` ist die Quelle der Wahrheit** für Protokoll, Datenmodell und Kameraverhalten.

- Lies die relevanten Spec-Abschnitte, bevor du etwas an Protokoll, Datenmodell oder Kameraverhalten baust.
- Weicht der Code von der Spec ab oder ist die Spec lückenhaft: **nicht still entscheiden.** Lücke beschreiben, Spec-Änderung vorschlagen, Freigabe abwarten.
- Spec-Abschnitte in Docstrings und Tests referenzieren (`# SPEC §4.3`).
- Die Protokollversion (`v1`) wird nie ohne ausdrückliche Anweisung geändert.

## Stack

| | |
|---|---|
| Sprache | Python ≥ 3.11 (Ziel: Raspberry Pi OS Trixie mit 3.13), Venv per `uv` |
| Gemeinsam | `uv`-Workspace, `ruff`, `pyright` strict, `pytest` |
| Protokoll | `packages/protocol`: Pydantic-v2-Modelle, von Agent **und** Hub genutzt |
| Agent | `numpy`, `Pillow`; Kameras hinter dem `Camera`-Interface (ZWO-SDK, libcamera, Sim) |
| Hub (ab M2) | FastAPI, PostgreSQL, Jinja2 + HTMX (wie myboxi) |
| App (ab M3) | Flutter, Builds über Codemagic |

Neue Abhängigkeiten nur mit Begründung; der Agent läuft auf einem Pi.

## Lizenzen (REUSE)

| Pfad | Lizenz |
|---|---|
| Standard (Hub, Tools, Deployment) | AGPL-3.0-or-later |
| `agent/`, `image/` | GPL-3.0-or-later |
| `packages/protocol/` | Apache-2.0 |
| `app/` | Apache-2.0 (GPL verträgt sich nicht mit den App-Store-Bedingungen) |
| `docs/`, `README.md`, `CLAUDE.md` | CC-BY-4.0 |

Neue Pfade mit anderer Lizenz in `REUSE.toml` eintragen.

## Repo-Layout

```
docs/SPEC.md            Vertrag
docs/research.md        Schmerzpunkt- und Marktanalyse
packages/protocol/      Pydantic-Modelle: Envelope, Hello, Status, Frame, Event, Command
agent/
  src/allskyhub_agent/
    core/               Reine Logik: Sonnenstand, Tag/Nacht, Auto-Belichtung
    adapters/           Hardware hinter Interfaces: camera (sim, später zwo, libcamera)
    store/              Bildablage, Night-ID, Aufräumen
  tests/
server/                 Hub (ab M2)
app/                    Flutter-App (ab M3)
```

## Befehle

```bash
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run pyright
uvx --from 'reuse[charset-normalizer]' reuse lint
uv run allskyhub-agent --sim --lat 48.14 --lon 14.39 run --frames 20 --data /tmp/ash
```

Vor jedem Commit: Tests, ruff, pyright und `reuse lint` grün.

**numpy-Falle:** Die CI prüft auch Python 3.13 mit numpy 2.5. Deren Typangaben für `np.clip`, `np.stack`, `np.repeat`, `np.roll` und `np.frombuffer` sind für pyright strict „partially unknown“; lokal mit numpy 2.4 fällt das nicht auf. Stattdessen ufuncs (`np.minimum`/`np.maximum`), `np.empty` + Zuweisung bzw. `np.ndarray(..., buffer=...)` nehmen. Prüfen mit `tools/pyright-numpy25.sh`.

## Architekturregeln

1. **Aufnahme hängt nie am Netz.** Der Agent nimmt auf, speichert und verarbeitet weiter, wenn der Hub nicht erreichbar ist.
2. **Hardware nur hinter Interfaces.** `core/` importiert nie Kamera-SDKs, `numpy`-freie Logik wo möglich; jeder Adapter hat eine Sim-Variante, alles läuft am Laptop mit `--sim`.
3. **Zeit ist injiziert.** Kein `datetime.now()` / `time.time()` in `core/`; die Uhr kommt als Parameter oder über das `Clock`-Interface.
4. **Belichtung an genau einer Stelle.** Nur der Auto-Exposure-Controller (SPEC §4.3) berechnet Belichtung und Gain.
5. **Nachrichten nur über `packages/protocol`.** Keine handgebauten Dicts für Protokollnachrichten.
