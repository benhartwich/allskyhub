# Agent updates

Cameras update their agent themselves (SPEC §8). This page describes how an update is made
and how the signing keys are handled.

## Flow

1. Raise `version` in `agent/pyproject.toml` and `agent/src/allskyhub_agent/__init__.py`,
   add what it brings to `agent/src/allskyhub_agent/update/news.json` (at the top, a few
   short points in German and English, for owners, not developers; SPEC §8 What's new),
   merge, then push a tag `agent-vX.Y.Z` on `main`.
2. The `image` workflow builds the image and the update bundle
   `allskyhub-agent-X.Y.Z-arm64.tar.xz`: the directory `X.Y.Z/` with its venv, exactly as
   the image installs it under `/opt/allskyhub-agent/releases/`.
3. The `release` job checks that the tag matches the agent version and has a news entry
   (`tools/release/release_notes.py`, which also writes the release notes), writes `manifest.json`
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
| Main (release) | `image/files/etc/allskyhub-agent/update-keys/allskyhub-2026-main.pem` | GitHub secret `ALLSKYHUB_UPDATE_KEY` and an offline backup with the project owner |
| Emergency | `…/update-keys/emergency/allskyhub-2026-backup.pem` | offline with the project owner only |

The camera trusts every `*.pem` in `/etc/allskyhub-agent/update-keys/` as a release key.
The emergency keys in `emergency/` sign only the key list (below), never a release. An
update replaces `/opt/allskyhub-agent`, never `/etc`: **cameras learn keys from an image or
from the key list**, so both keys must be in the image before cameras go out.

Creating the keys (once, on the owner's computer, never in the repository):

```bash
openssl genpkey -algorithm ed25519 -out allskyhub-2026-main.key
openssl genpkey -algorithm ed25519 -out allskyhub-2026-backup.key
openssl pkey -in allskyhub-2026-main.key -pubout \
  -out image/files/etc/allskyhub-agent/update-keys/allskyhub-2026-main.pem
openssl pkey -in allskyhub-2026-backup.key -pubout \
  -out image/files/etc/allskyhub-agent/update-keys/allskyhub-2026-backup.pem
gh secret set ALLSKYHUB_UPDATE_KEY --env release < allskyhub-2026-main.key
```

The secret belongs to the GitHub environment `release` only, never to the repository:
create the environment first (Settings → Environments → `release`) with yourself as
required reviewer and the deployment tags limited to `agent-v*`. Then every signing run
waits for your approval, and the workflow refuses a tag whose commit is not on `main`.

Commit the two `.pem` files; keep both `.key` files offline (e.g. a password manager).

## Key revocation

If the main key leaks (or is lost), revoke it and add a new one with the key list
(SPEC §8 Key revocation), on the owner's computer, with the offline emergency key:

```bash
openssl genpkey -algorithm ed25519 -out allskyhub-2027-main.key
openssl pkey -in allskyhub-2027-main.key -pubout -out allskyhub-2027-main.pem
K=image/files/etc/allskyhub-agent/update-keys
tools/release/make_keys.py 1 --revoke $K/allskyhub-2026-main.pem \
  --add allskyhub-2027-main.pem > keys.json
openssl pkeyutl -sign -rawin -inkey allskyhub-2026-backup.key -in keys.json -out keys.json.sig
openssl pkeyutl -verify -pubin -inkey $K/emergency/allskyhub-2026-backup.pem -rawin \
  -in keys.json -sigfile keys.json.sig
gh release upload channel-stable keys.json keys.json.sig --clobber
gh secret set ALLSKYHUB_UPDATE_KEY --env release < allskyhub-2027-main.key
```

Then commit `allskyhub-2027-main.pem` next to the old key in `update-keys/` (new images
trust it from the start) and release a new version, signed with the new key. Cameras fetch
the list before every update, so they refuse anything the old key signs from then on.

- Each list is complete: a later one (serial 2, 3, …) lists every key revoked so far.
  Cameras take only a higher serial and keep the newest even if it is removed from the
  channel.
- **Emergency key leaked:** it could add its own release key. Build an image with a new
  emergency key pair; cameras with the old image cannot be protected without it.
- **Never** put a private key into the repository, logs or artifacts.

## Own channel

Set `ALLSKYHUB_UPDATE_MANIFEST_URL` in `/etc/allskyhub-agent/updater.env` and put your
public key into `/etc/allskyhub-agent/update-keys/`.
