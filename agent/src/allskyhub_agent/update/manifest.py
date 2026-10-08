"""Fetch and verify the channel manifest (SPEC §8).

The signature is Ed25519 over the manifest's exact bytes, published next to it as
`manifest.json.sig`. The camera trusts every public key (PEM) in its keys directory,
which comes with the image; an update never changes it.
"""

from __future__ import annotations

from pathlib import Path

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from pydantic import ValidationError

from allskyhub_protocol.updates import UpdateManifest

MAX_MANIFEST_BYTES = 64 * 1024


class ManifestError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def trusted_keys(keys_dir: Path) -> list[Ed25519PublicKey]:
    keys: list[Ed25519PublicKey] = []
    for pem in sorted(keys_dir.glob("*.pem")):
        try:
            key = load_pem_public_key(pem.read_bytes())
        except (OSError, ValueError):
            continue
        if isinstance(key, Ed25519PublicKey):
            keys.append(key)
    return keys


def verify_signature(data: bytes, signature: bytes, keys_dir: Path) -> bool:
    for key in trusted_keys(keys_dir):
        try:
            key.verify(signature, data)
        except InvalidSignature:
            continue
        return True
    return False


def _get(client: httpx.Client, url: str) -> bytes:
    response = client.get(url, follow_redirects=True)
    response.raise_for_status()
    if len(response.content) > MAX_MANIFEST_BYTES:
        raise ManifestError("manifest_too_large")
    return response.content


def fetch_manifest(client: httpx.Client, url: str, keys_dir: Path) -> UpdateManifest:
    """Raises httpx.HTTPError when offline (or nothing is published) and ManifestError
    when the manifest is not valid."""
    if not trusted_keys(keys_dir):
        raise ManifestError("no_keys")
    data = _get(client, url)
    signature = _get(client, url + ".sig")
    if not verify_signature(data, signature, keys_dir):
        raise ManifestError("bad_signature")
    try:
        return UpdateManifest.model_validate_json(data)
    except ValidationError:
        raise ManifestError("bad_manifest") from None
