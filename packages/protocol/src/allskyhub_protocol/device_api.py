"""HTTP bodies of the device API and device identity (SPEC §6.1, §6.2, §6.6).

These travel as plain JSON over HTTPS, not in an `Envelope`. Signing needs Ed25519 (the
agent uses `cryptography`); deriving ids and building the signed payload need only the
standard library, so this module has no extra dependency.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from enum import StrEnum
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from allskyhub_protocol.models import PRODUCT_NAMES

# SPEC §6.2: lowercase RFC 4648 base32 of 16 bytes, without padding.
DEVICE_ID_PATTERN = r"^[a-z2-7]{26}$"
# SPEC §6.2: no 0/O, 1/I/L; shown as "ABC-DEF" (the hyphen is optional on input).
PAIRING_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
PAIRING_CODE_LENGTH = 6
_B64URL = r"^[A-Za-z0-9_-]+$"


class _Model(BaseModel):
    # SPEC §6.1: receivers ignore fields they do not know, so either side can add one.
    model_config = ConfigDict(extra="ignore", frozen=True)


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


class HubKey(_Model):
    """One of the hub's Ed25519 keys for local logins (SPEC §6.2 step 6)."""

    kid: str = Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")
    public_key: str = Field(pattern=_B64URL, min_length=43, max_length=43)


class TokenResponse(_Model):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105
    expires_in: int = Field(gt=0)
    # SPEC §6.2 step 6: the agent replaces its stored list with every answer.
    hub_keys: list[HubKey] = Field(default_factory=list[HubKey])


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
    # One of PRODUCT_NAMES (SPEC §5.2), night and day products alike.
    name: str = Field(max_length=40)
    variant: FrameVariant = FrameVariant.FULL

    @field_validator("name")
    @classmethod
    def _known_product(cls, value: str) -> str:
        if value not in {name for name, _ in PRODUCT_NAMES.values()}:
            raise ValueError("not a product name (SPEC §5.2)")
        return value


class UploadEventArgs(_Model):
    """`args` of the `upload_event` command (SPEC §6.5)."""

    night_id: str = Field(pattern=r"^\d{8}$")
    event_id: str = Field(pattern=r"^[a-z]+-\d{8}T\d{6}Z(-\d+)?$")
    variant: FrameVariant = FrameVariant.FULL


CameraChoice = Literal["auto", "zwo-asi678mc", "rpi-hq", "sim"]


class SetSettingsArgs(_Model):
    """`args` of the `set_settings` command (SPEC §6.5): only the given keys change.

    The fixed keys are checked here; further keys from the device's settings schema come
    as extra fields (JSON scalars) and are checked with `invalid_settings` (SPEC §6.9)."""

    model_config = ConfigDict(extra="allow", frozen=True)
    __pydantic_extra__: dict[str, str | int | float | bool | None] = Field(  # pyright: ignore[reportIncompatibleVariableOverride]
        init=False
    )

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


# SPEC §6.5 local_access: the lowest Argon2id parameters the device accepts.
ARGON2_MIN_MEMORY_KIB = 19456
ARGON2_MIN_TIME = 2
_ARGON2 = re.compile(r"^\$argon2id\$v=19\$m=(\d+),t=(\d+),p=(\d+)\$[A-Za-z0-9+/]+\$[A-Za-z0-9+/]+$")


class LocalAccessArgs(_Model):
    """`args` of the `local_access` command (SPEC §6.5): the offline code's verifier."""

    verifier: str | None = Field(max_length=512)

    @field_validator("verifier")
    @classmethod
    def _argon2id(cls, v: str | None) -> str | None:
        if v is None:
            return v
        match = _ARGON2.fullmatch(v)
        if match is None:
            raise ValueError("not an Argon2id PHC string")
        if int(match[1]) < ARGON2_MIN_MEMORY_KIB or int(match[2]) < ARGON2_MIN_TIME:
            raise ValueError("Argon2id parameters too weak")
        return v


# SPEC §7.4 B: 12 characters from the pairing alphabet, shown as XXXX-XXXX-XXXX.
OFFLINE_CODE_LENGTH = 12


def normalize_offline_code(code: str) -> str:
    """Upper-case, without spaces and hyphens, like a pairing code."""
    return normalize_pairing_code(code)


# SPEC §7.4 A step 4: the assertion lives at most this long.
LOCAL_LOGIN_MAX_AGE_S = 60


class LocalLoginClaims(_Model):
    """Payload of a local-login assertion (SPEC §7.4 A step 4)."""

    kid: str = Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")
    aud: str = Field(pattern=DEVICE_ID_PATTERN)
    sub: str = Field(min_length=1, max_length=64)
    nonce: str = Field(pattern=_B64URL, min_length=16, max_length=128)
    iat: int
    exp: int

    @model_validator(mode="after")
    def _short(self) -> LocalLoginClaims:
        if not 0 < self.exp - self.iat <= LOCAL_LOGIN_MAX_AGE_S:
            raise ValueError("exp must be at most 60 s after iat")
        return self


def local_login_signing_payload(payload: str) -> bytes:
    """SPEC §7.4 A step 4: the bytes the hub signs, ``payload`` being the base64url part."""
    return f"allskyhub-v1\nlocal-login\n{payload}".encode()


def encode_local_login_claims(claims: LocalLoginClaims) -> str:
    """The base64url payload part of an assertion."""
    return b64url(json.dumps(claims.model_dump(), separators=(",", ":")).encode())


def split_local_login_assertion(assertion: str) -> tuple[str, LocalLoginClaims, bytes]:
    """``<payload>.<signature>`` → (payload part, claims, raw signature). Checks the form
    only; the caller verifies the signature with ``local_login_signing_payload(payload)``
    and the hub key ``claims.kid``. Raises ``ValueError``."""
    if len(assertion) > 2048 or assertion.count(".") != 1:
        raise ValueError("not an assertion")
    payload, signature = assertion.split(".")
    try:
        claims = LocalLoginClaims.model_validate_json(b64url_decode(payload))
    except ValidationError as exc:
        raise ValueError("invalid claims") from exc
    return payload, claims, b64url_decode(signature)


class PublicSections(_Model):
    """Which parts of the public page are shown (SPEC §6.8)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    live: bool = True
    loop: bool = True
    archive: bool = True
    events: bool = True
    sky: bool = True
    aurora: bool = True
    # SPEC §6.8: what the telescope next to the camera does (roadmap C15), opt-in.
    observatory: bool = False
    # SPEC §6.8, §7.8: aircraft over the live image (roadmap H3).
    aircraft: bool = True


class PublicProfile(_Model):
    """What the owner writes about the camera (SPEC §6.8); empty strings are not shown."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    title: str = Field(default="", max_length=100)
    location_label: str = Field(default="", max_length=200)
    show_coordinates: bool = False
    camera: str = Field(default="", max_length=200)
    lens: str = Field(default="", max_length=200)
    computer: str = Field(default="", max_length=200)
    owner: str = Field(default="", max_length=200)
    notes: str = Field(default="", max_length=2000)
    sections: PublicSections = Field(default_factory=PublicSections)


class PublicState(_Model):
    """Answer of `GET` and `PUT /device/v1/public` (SPEC §6.8)."""

    enabled: bool
    url: str | None
    profile: PublicProfile

    @classmethod
    def from_hub(cls, raw: str | bytes) -> PublicState:
        """The hub's answer, ignoring profile keys and sections this version does not know
        (SPEC §6.1: unknown fields are ignored; the forbid is only for the owner's input)."""
        data: object = json.loads(raw)
        if isinstance(data, dict):
            d = cast("dict[str, Any]", data)
            prof: object = d.get("profile")
            if isinstance(prof, dict):
                p = {k: v for k, v in cast("dict[str, Any]", prof).items()
                     if k in PublicProfile.model_fields}  # fmt: skip
                sec: object = p.get("sections")
                if isinstance(sec, dict):
                    p["sections"] = {k: v for k, v in cast("dict[str, Any]", sec).items()
                                     if k in PublicSections.model_fields}  # fmt: skip
                d = {**d, "profile": p}
            data = d
        return cls.model_validate(data)


class PublicUpdate(_Model):
    """Body of `PUT /device/v1/public` (SPEC §6.8): `profile` replaces the whole profile."""

    # Input the owner typed: an unknown key is a mistake and answered with 422 (SPEC §6.8).
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool | None = None
    profile: PublicProfile | None = None
