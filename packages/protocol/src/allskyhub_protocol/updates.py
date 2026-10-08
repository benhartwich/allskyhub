"""Update channel manifest (SPEC §8): JSON next to its detached Ed25519 signature.

The manifest is published at a fixed URL per channel; the camera verifies the signature
against its trusted keys before reading it.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

VERSION_PATTERN = r"^\d+\.\d+\.\d+$"
_VERSION = re.compile(VERSION_PATTERN)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


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


def version_key(version: str) -> tuple[int, int, int]:
    """Orders `major.minor.patch`; anything else sorts first (never an upgrade)."""
    m = _VERSION.match(version)
    if m is None:
        return (-1, -1, -1)
    a, b, c = version.split(".")
    return (int(a), int(b), int(c))


def is_newer(candidate: str, current: str) -> bool:
    return version_key(candidate) > version_key(current)
