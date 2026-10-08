"""HTTP bodies of the device API and device identity (SPEC §6.1, §6.2, §6.6).

These travel as plain JSON over HTTPS, not in an `Envelope`. Signing needs Ed25519 (the
agent uses `cryptography`); deriving ids and building the signed payload need only the
standard library, so this module has no extra dependency.
"""

from __future__ import annotations

import base64
import hashlib
import re
from enum import StrEnum
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# SPEC §6.2: lowercase RFC 4648 base32 of 16 bytes, without padding.
DEVICE_ID_PATTERN = r"^[a-z2-7]{26}$"
# SPEC §6.2: no 0/O, 1/I/L; shown as "ABC-DEF" (the hyphen is optional on input).
PAIRING_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
PAIRING_CODE_LENGTH = 6
_B64URL = r"^[A-Za-z0-9_-]+$"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def b64url(data: bytes) -> str:
    """Base64url without padding (SPEC §6.2)."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64url_decode(text: str) -> bytes:
    """Inverse of `b64url`; raises `ValueError` on invalid input."""
    if not re.fullmatch(_B64URL, text):
        raise ValueError("not base64url")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def device_id_from_public_key(public_key: bytes) -> str:
    """SPEC §6.2: device id of a raw 32-byte Ed25519 public key."""
    if len(public_key) != 32:
        raise ValueError("an Ed25519 public key has 32 bytes")
    digest = hashlib.sha256(public_key).digest()[:16]
    return base64.b32encode(digest).decode().rstrip("=").lower()


class Purpose(StrEnum):
    """What a signed challenge is used for (SPEC §6.2)."""

    REGISTER = "register"
    TOKEN = "token"  # noqa: S105


def signing_payload(purpose: Purpose, device_id: str, nonce: str) -> bytes:
    """SPEC §6.2: the exact bytes the device signs with its Ed25519 key."""
    return f"allskyhub-v1\n{purpose.value}\n{device_id}\n{nonce}".encode()


def normalize_pairing_code(code: str) -> str:
    """Upper-case, without spaces and hyphens: "abc-def" → "ABCDEF"."""
    return re.sub(r"[\s-]", "", code).upper()


class ChallengeRequest(_Model):
    """`POST /device/v1/challenge` (SPEC §6.6)."""

    device_id: str = Field(pattern=DEVICE_ID_PATTERN)


class ChallengeResponse(_Model):
    nonce: str = Field(min_length=16, max_length=128)
    expires_in: int = Field(gt=0)


class RegisterRequest(_Model):
    """`POST /device/v1/register` (SPEC §6.2, §6.6)."""

    public_key: str = Field(pattern=_B64URL, description="raw Ed25519 key, base64url")
    profile: str = Field(min_length=1, max_length=64)
    agent_version: str = Field(min_length=1, max_length=64)
    nonce: str = Field(min_length=16, max_length=128)
    signature: str = Field(pattern=_B64URL, description="Ed25519 signature, base64url")


class RegisterResponse(_Model):
    device_id: str = Field(pattern=DEVICE_ID_PATTERN)
    paired: bool
    # Present while the device is not paired (SPEC §6.2 step 2).
    pairing_code: str | None = None
    expires_in: int | None = Field(default=None, gt=0)

    @field_validator("pairing_code")
    @classmethod
    def _code_alphabet(cls, v: str | None) -> str | None:
        if v is not None and (
            len(v) != PAIRING_CODE_LENGTH or any(c not in PAIRING_CODE_ALPHABET for c in v)
        ):
            raise ValueError("invalid pairing code")
        return v


class TokenRequest(_Model):
    """`POST /device/v1/token` (SPEC §6.1, §6.6)."""

    device_id: str = Field(pattern=DEVICE_ID_PATTERN)
    nonce: str = Field(min_length=16, max_length=128)
    signature: str = Field(pattern=_B64URL)


class TokenResponse(_Model):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105
    expires_in: int = Field(gt=0)


class FrameVariant(StrEnum):
    """Which image of a frame to upload (SPEC §6.5)."""

    FULL = "full"
    THUMB = "thumb"


class UploadFrameArgs(_Model):
    """`args` of the `upload_frame` command (SPEC §6.5)."""

    night_id: str = Field(pattern=r"^\d{8}$")
    name: str = Field(pattern=r"^[A-Za-z0-9._-]{1,128}$")
    variant: FrameVariant = FrameVariant.FULL


class UploadProductArgs(_Model):
    """`args` of the `upload_product` command (SPEC §6.5)."""

    night_id: str = Field(pattern=r"^\d{8}$")
    name: Literal["keogram.jpg", "startrails.jpg", "timelapse.mp4"]
    variant: FrameVariant = FrameVariant.FULL


class UploadEventArgs(_Model):
    """`args` of the `upload_event` command (SPEC §6.5)."""

    night_id: str = Field(pattern=r"^\d{8}$")
    event_id: str = Field(pattern=r"^[a-z]+-\d{8}T\d{6}Z(-\d+)?$")
    variant: FrameVariant = FrameVariant.FULL


CameraChoice = Literal["auto", "zwo-asi678mc", "rpi-hq", "sim"]


class SetSettingsArgs(_Model):
    """`args` of the `set_settings` command (SPEC §6.5): only the given keys change."""

    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    timezone: str | None = None
    camera: CameraChoice | None = None
    day_delay_s: float | None = Field(default=None, ge=0, le=3600)
    night_delay_s: float | None = Field(default=None, ge=0, le=3600)

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str | None) -> str | None:
        if v is not None:
            try:
                ZoneInfo(v)
            except (ZoneInfoNotFoundError, ValueError):
                raise ValueError("unknown time zone") from None
        return v

    @model_validator(mode="after")
    def _location_pair(self) -> SetSettingsArgs:
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude go together")
        return self


class ErrorCode(StrEnum):
    """`code` of an `error` reply to a command (SPEC §6.5)."""

    NOT_FOUND = "not_found"
    INVALID_ARGS = "invalid_args"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


class CloseCode:
    """WebSocket close codes of the device API (SPEC §6.1)."""

    REAUTH = 4401  # token expired or invalid: get a new one, reconnect
    UNPAIRED = 4403  # device no longer paired: register again (SPEC §6.2)
    REPLACED = 4409  # a newer connection of the same device took over
