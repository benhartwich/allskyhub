"""Device identity (SPEC §6.2): Ed25519 key on disk, device id, challenge signatures."""

from __future__ import annotations

import os
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_pem_private_key,
)

from allskyhub_protocol import Purpose, b64url, device_id_from_public_key, signing_payload

DEFAULT_KEY_PATH = Path("/var/lib/allskyhub-agent/device.key")


class DeviceIdentity:
    def __init__(self, key: Ed25519PrivateKey) -> None:
        self._key = key
        self.public_key = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        self.device_id = device_id_from_public_key(self.public_key)

    @classmethod
    def load_or_create(cls, path: Path) -> DeviceIdentity:
        """Load the key from `path`, or create it there (mode 0600) on first start."""
        if path.exists():
            key = load_pem_private_key(path.read_bytes(), password=None)
            if not isinstance(key, Ed25519PrivateKey):
                raise ValueError(f"{path} does not hold an Ed25519 key")
            return cls(key)
        key = Ed25519PrivateKey.generate()
        pem = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(pem)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(path)  # a power cut never leaves a half-written key behind
        return cls(key)

    @property
    def public_key_b64(self) -> str:
        return b64url(self.public_key)

    def sign(self, purpose: Purpose, nonce: str) -> str:
        return b64url(self._key.sign(signing_payload(purpose, self.device_id, nonce)))
