# Agent updates

Cameras update their agent themselves (SPEC §8). This page describes how an update is made
and how the signing keys are handled.

## Flow

1. Raise `version` in `agent/pyproject.toml` and `agent/src/allskyhub_agent/__init__.py`,
   merge, then push a tag `agent-vX.Y.Z` on `main`.
2. The `image` workflow builds the image and the update bundle
   `allskyhub-agent-X.Y.Z-arm64.tar.xz`: the directory `X.Y.Z/` with its venv, exactly as
   the image installs it under `/opt/allskyhub-agent/releases/`.
3. The `release` job checks that the tag matches the agent version, writes `manifest.json`
   (`tools/release/make_manifest.py`: version, URL, SHA-256 and size of the bundle), signs
   it with the secret `ALLSKYHUB_UPDATE_KEY` (Ed25519) and checks the signature against
   the public keys in `image/files/etc/allskyhub-agent/update-keys/`.
4. The bundle goes into the release `agent-vX.Y.Z`; `manifest.json` and
   `manifest.json.sig` into the release `channel-stable`. Every camera reads
   `https://github.com/benhartwich/allskyhub/releases/download/channel-stable/manifest.json`.

On the camera `allskyhub-updater.timer` runs the updater 10 minutes after boot and then
hourly. It installs only with a valid signature, only newer versions, and only during the
day, so a night's capture is never interrupted. It unpacks the bundle next to the running
version, switches `/opt/allskyhub-agent/current` and restarts the agent. If the new agent
does not report its version over the local web UI (and capture, if the old one captured)
within 5 minutes, it switches back and does not try that version again. The state is in
`/var/lib/allskyhub-updater/state.json`.

## Keys

| Key | Public (in the image) | Private |
|---|---|---|
| Main | `image/files/etc/allskyhub-agent/update-keys/allskyhub-2026-main.pem` | GitHub secret `ALLSKYHUB_UPDATE_KEY` and an offline backup with the project owner |
| Emergency | `…/allskyhub-2026-backup.pem` | offline backup with the project owner only |

The camera trusts every `*.pem` in `/etc/allskyhub-agent/update-keys/`. An update replaces
`/opt/allskyhub-agent`, never `/etc`: **cameras learn keys only from an image**, so the keys
must be in the image before cameras go out.

Creating the keys (once, on the owner's computer, never in the repository):

```bash
openssl genpkey -algorithm ed25519 -out allskyhub-2026-main.key
openssl genpkey -algorithm ed25519 -out allskyhub-2026-backup.key
openssl pkey -in allskyhub-2026-main.key -pubout \
  -out image/files/etc/allskyhub-agent/update-keys/allskyhub-2026-main.pem
openssl pkey -in allskyhub-2026-backup.key -pubout \
  -out image/files/etc/allskyhub-agent/update-keys/allskyhub-2026-backup.pem
gh secret set ALLSKYHUB_UPDATE_KEY < allskyhub-2026-main.key
```

Commit the two `.pem` files; keep both `.key` files offline (e.g. a password manager).

- **Main key lost or leaked:** put the emergency key into the secret, release, and build an
  image with a new key pair.
- **Never** put a private key into the repository, logs or artifacts.

## Own channel

Set `ALLSKYHUB_UPDATE_MANIFEST_URL` in `/etc/allskyhub-agent/updater.env` and put your
public key into `/etc/allskyhub-agent/update-keys/`.
