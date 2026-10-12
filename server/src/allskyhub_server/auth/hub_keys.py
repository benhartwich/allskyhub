"""The hub's Ed25519 keys for local logins (SPEC §6.2 step 6, §7.4 A).

Keys live in ``Settings.key_dir`` as ``<kid>.key`` (raw 32-byte private key, mode 0600).
The newest one signs; all of them are announced in the token answer, so an old key keeps
working for cameras until it is deleted (at least 30 days after a rotation). The first key
is made on first use.
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from allskyhub_protocol import (
    HubKey,
    LocalLoginClaims,
    b64url,
    encode_local_login_claims,
    local_login_signing_payload,
)


def _raw_public(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def kid_for(key: Ed25519PrivateKey) -> str:
    """Short id derived from the public key, so the same file always has the same id."""
    return b64url(hashlib.sha256(_raw_public(key)).digest()[:9])


@dataclass(frozen=True)
class _Key:
    kid: str
    private: Ed25519PrivateKey
    mtime: float


class HubKeys:
    def __init__(self, key_dir: Path) -> None:
        self._dir = key_dir

    def _load(self) -> list[_Key]:
        keys: list[_Key] = []
        if self._dir.is_dir():
            for path in self._dir.glob("*.key"):
                raw = path.read_bytes()
                if len(raw) != 32:
                    continue
                private = Ed25519PrivateKey.from_private_bytes(raw)
                keys.append(_Key(kid_for(private), private, path.stat().st_mtime))
        return sorted(keys, key=lambda k: k.mtime)

    def _keys(self) -> list[_Key]:
        keys = self._load()
        if not keys:
            self.rotate()
            keys = self._load()
        return keys

    def rotate(self) -> str:
        """Make a new signing key; returns its kid. Old keys stay until removed by hand."""
        self._dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        private = Ed25519PrivateKey.generate()
        kid = kid_for(private)
        raw = private.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        path = self._dir / f"{kid}.key"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
        os.utime(path, (time.time(), time.time()))
        return kid

    def public(self) -> list[HubKey]:
        """For the token answer, newest first."""
        return [
            HubKey(kid=k.kid, public_key=b64url(_raw_public(k.private)))
            for k in reversed(self._keys())
        ]

    def sign(self, *, device_id: str, user_id: str, nonce: str, now: int) -> str:
        """A local-login assertion (SPEC §7.4 A step 4), valid 60 s."""
        key = self._keys()[-1]
        claims = LocalLoginClaims(
            kid=key.kid, aud=device_id, sub=user_id, nonce=nonce, iat=now, exp=now + 60
        )
        payload = encode_local_login_claims(claims)
        signature = key.private.sign(local_login_signing_payload(payload))
        return f"{payload}.{b64url(signature)}"
