"""Update channel manifest and key list (SPEC §8): JSON next to its detached Ed25519
signature.

The manifest is published at a fixed URL per channel; the camera verifies the signature
against its release keys before reading it. The key list next to it, signed by an
emergency key, revokes release keys and adds new ones.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

VERSION_PATTERN = r"^\d+\.\d+\.\d+$"
_VERSION = re.compile(VERSION_PATTERN)


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class Bundle(_Model):
    """The agent release: a tar.xz holding the directory `<version>/` with its venv."""

    url: str = Field(pattern=r"^https://", max_length=2048)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(gt=0, le=2**31)


class UpdateManifest(_Model):
    channel: Literal["stable"] = "stable"
    version: str = Field(pattern=VERSION_PATTERN, max_length=32)
    released_at: AwareDatetime
    bundle: Bundle


# Base64 of an Ed25519 public key's SubjectPublicKeyInfo (DER), the body of its PEM file.
KEY_PATTERN = r"^MCowBQYDK2VwAyEA[A-Za-z0-9+/]{43}=$"


class KeyList(_Model):
    """`keys.json` (SPEC §8 Key revocation): the complete list, each one replacing the one
    before; the camera takes only a higher `serial`."""

    serial: int = Field(ge=1)
    issued_at: AwareDatetime
    revoked: list[str] = Field(default_factory=list[str], max_length=32)  # SHA-256 of the DER
    added: list[str] = Field(default_factory=list[str], max_length=8)

    @field_validator("revoked")
    @classmethod
    def _fingerprints(cls, v: list[str]) -> list[str]:
        if not all(re.fullmatch(r"[0-9a-f]{64}", f) for f in v):
            raise ValueError("revoked: SHA-256 fingerprints in lower-case hex")
        return v

    @field_validator("added")
    @classmethod
    def _keys(cls, v: list[str]) -> list[str]:
        if not all(re.fullmatch(KEY_PATTERN, k) for k in v):
            raise ValueError("added: Ed25519 public keys, base64 of the DER")
        return v


def version_key(version: str) -> tuple[int, int, int]:
    """Orders `major.minor.patch`; anything else sorts first (never an upgrade)."""
    m = _VERSION.match(version)
    if m is None:
        return (-1, -1, -1)
    a, b, c = version.split(".")
    return (int(a), int(b), int(c))


def is_newer(candidate: str, current: str) -> bool:
    return version_key(candidate) > version_key(current)
