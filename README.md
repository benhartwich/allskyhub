# allskyhub

A plug-and-play all-sky camera: a Raspberry Pi with a tested camera, a ready-made image, pairing
from your phone and an outbound connection to a hub. No installer, no FTP, no port forwarding.

**Status:** early development (milestone M0). Not usable yet.

- Website and hosted hub: [allskyhub.org](https://allskyhub.org) (coming)
- Specification: [docs/SPEC.md](docs/SPEC.md)
- Why this exists: [docs/research.md](docs/research.md)

## Parts

| Path | What | License |
|---|---|---|
| `agent/` | Camera agent on the Pi: capture, auto exposure, day/night, storage | GPL-3.0-or-later |
| `packages/protocol/` | Device protocol message models | Apache-2.0 |
| `server/` | Hub: pairing, live image, public pages, alerts (M2) | AGPL-3.0-or-later |
| `app/` | Android and iOS app, Flutter, built with Codemagic (M3) | Apache-2.0 |
| `docs/` | Specification and documentation | CC-BY-4.0 |

Licensing follows [REUSE](https://reuse.software); see `REUSE.toml` and `LICENSES/`.

## Try the simulator

```bash
uv sync
uv run allskyhub-agent --sim --lat 48.14 --lon 14.39 \
  run --frames 20 --data /tmp/allskyhub --start 2026-10-06T18:00:00+02:00
```

A simulated camera runs through dusk: watch the agent switch to night mode and ramp exposure and
gain.

## Development

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run pyright
uvx --from 'reuse[charset-normalizer]' reuse lint
```
