"""Fetch and verify the channel manifest and its key list (SPEC §8).

The signature is Ed25519 over the manifest's exact bytes, published next to it as
`manifest.json.sig`. The camera trusts the release keys (PEM) in its keys directory, which
comes with the image; an update never changes it. The emergency keys in its `emergency/`
folder sign only `keys.json` (Key revocation): it revokes release keys and adds new ones.
The newest accepted list is kept next to the updater's state, so a list taken off the
channel, or an older one put back, never brings a revoked key back.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from pathlib import Path

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    load_der_public_key,
    load_pem_public_key,
)
from pydantic import ValidationError

from allskyhub_protocol.updates import KeyList, UpdateManifest

log = logging.getLogger(__name__)

MAX_MANIFEST_BYTES = 64 * 1024
EMERGENCY = "emergency"
KEYS_NAME = "keys.json"


class ManifestError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _pem_keys(folder: Path) -> list[Ed25519PublicKey]:
    keys: list[Ed25519PublicKey] = []
    for pem in sorted(folder.glob("*.pem")):
        try:
            key = load_pem_public_key(pem.read_bytes())
        except (OSError, ValueError):
            continue
        if isinstance(key, Ed25519PublicKey):
            keys.append(key)
    return keys


def fingerprint(key: Ed25519PublicKey) -> str:
    """SHA-256 of the key's SubjectPublicKeyInfo (DER), as `keys.json` names it."""
    der = key.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    return hashlib.sha256(der).hexdigest()


def _verify(keys: list[Ed25519PublicKey], data: bytes, signature: bytes) -> bool:
    for key in keys:
        try:
            key.verify(signature, data)
        except InvalidSignature:
            continue
        return True
    return False


def read_key_list(data: bytes, signature: bytes, keys_dir: Path) -> KeyList | None:
    """The key list if an emergency key signed it, else None."""
    if not _verify(_pem_keys(keys_dir / EMERGENCY), data, signature):
        return None
    try:
        return KeyList.model_validate_json(data)
    except ValidationError:
        return None


def stored_key_list(keys_dir: Path, store: Path | None) -> KeyList | None:
    if store is None:
        return None
    try:
        data = (store / KEYS_NAME).read_bytes()
        signature = (store / (KEYS_NAME + ".sig")).read_bytes()
    except OSError:
        return None
    return read_key_list(data, signature, keys_dir)


def trusted_keys(keys_dir: Path, store: Path | None = None) -> list[Ed25519PublicKey]:
    """The release keys: the image's and those the key list added, without the revoked."""
    keys = _pem_keys(keys_dir)
    listed = stored_key_list(keys_dir, store)
    if listed is None:
        return keys
    for b64 in listed.added:
        try:
            key = load_der_public_key(base64.b64decode(b64))
        except ValueError:
            continue
        if isinstance(key, Ed25519PublicKey):
            keys.append(key)
    revoked = set(listed.revoked)
    return [k for k in keys if fingerprint(k) not in revoked]


def verify_signature(
    data: bytes, signature: bytes, keys_dir: Path, store: Path | None = None
) -> bool:
    return _verify(trusted_keys(keys_dir, store), data, signature)


def update_key_list(client: httpx.Client, url: str, keys_dir: Path, store: Path) -> None:
    """Fetch `keys.json` next to the manifest and keep it if it is newer than the kept one.
    Offline, nothing published or not signed by an emergency key: the kept list stays."""
    base = url.rsplit("/", 1)[0] + "/" + KEYS_NAME
    try:
        data = _get(client, base)
        signature = _get(client, base + ".sig")
    except (httpx.HTTPError, ManifestError):
        return
    listed = read_key_list(data, signature, keys_dir)
    if listed is None:
        log.warning("%s is not signed by an emergency key; ignored", base)
        return
    kept = stored_key_list(keys_dir, store)
    if kept is not None and listed.serial <= kept.serial:
        return
    store.mkdir(parents=True, exist_ok=True)
    for name, body in ((KEYS_NAME + ".sig", signature), (KEYS_NAME, data)):
        tmp = store / (name + ".tmp")
        tmp.write_bytes(body)
        tmp.replace(store / name)
    log.warning("update key list %d: %d revoked, %d added", listed.serial,
                len(listed.revoked), len(listed.added))  # fmt: skip


def _get(client: httpx.Client, url: str) -> bytes:
    response = client.get(url, follow_redirects=True)
    response.raise_for_status()
    if len(response.content) > MAX_MANIFEST_BYTES:
        raise ManifestError("manifest_too_large")
    return response.content


def fetch_manifest(
    client: httpx.Client, url: str, keys_dir: Path, store: Path | None = None
) -> UpdateManifest:
    """Raises httpx.HTTPError when offline (or nothing is published) and ManifestError
    when the manifest is not valid. `store`: where the key list is kept (None: no list)."""
    if store is not None:
        update_key_list(client, url, keys_dir, store)
    if not trusted_keys(keys_dir, store):
        raise ManifestError("no_keys")
    data = _get(client, url)
    signature = _get(client, url + ".sig")
    if not verify_signature(data, signature, keys_dir, store):
        raise ManifestError("bad_signature")
    try:
        return UpdateManifest.model_validate_json(data)
    except ValidationError:
        raise ManifestError("bad_manifest") from None
