"""Message models for the device protocol v1 (SPEC §6).

Every message travels in an `Envelope` (SPEC §6.1). The envelope's `type` names the
body model; `Envelope.wrap()` builds one from a body, `parse_envelope()` reads one.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar, Literal, cast
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

PROTOCOL_VERSION: Literal[1] = 1


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    TYPE: ClassVar[str]


class Mode(StrEnum):
    """Capture mode (SPEC §4.2)."""

    DAY = "day"
    NIGHT = "night"


class Hello(_Body):
    """First message after connecting (SPEC §6.3)."""

    TYPE: ClassVar[str] = "hello"
    device_id: str = Field(pattern=r"^[a-z2-7]{26}$")  # SPEC §6.2
    profile: str
    agent_version: str
    capabilities: list[str] = Field(default_factory=list[str])


class Status(_Body):
    """Periodic device status (SPEC §6.3)."""

    TYPE: ClassVar[str] = "status"
    mode: Mode
    exposure_us: int = Field(ge=0)
    gain: float = Field(ge=0)
    mean: float = Field(ge=0, le=1)
    sensor_temp_c: float | None = None
    cpu_temp_c: float | None = None
    disk_free_pct: float | None = Field(default=None, ge=0, le=100)
    uptime_s: int = Field(ge=0)
    # SPEC §4.4: whether the system clock is NTP-synchronized (no RTC on a Pi).
    time_trusted: bool


class FrameInfo(_Body):
    """Metadata of one captured frame (SPEC §4.4)."""

    TYPE: ClassVar[str] = "frame"
    captured_at: AwareDatetime
    night_id: str = Field(pattern=r"^\d{8}$")
    name: str
    mode: Mode
    exposure_us: int = Field(ge=0)
    gain: float = Field(ge=0)
    mean: float = Field(ge=0, le=1)
    sun_elevation: float = Field(ge=-90, le=90)
    sensor_temp_c: float | None = None
    profile: str


class EventKind(StrEnum):
    """Detection kinds (SPEC §6.4)."""

    METEOR = "meteor"
    LIGHTNING = "lightning"
    AURORA = "aurora"
    NLC = "nlc"
    SATELLITE = "satellite"
    CLOUDS = "clouds"
    SKY_QUALITY = "sky_quality"


EVENT_ID_PATTERN = r"^[a-z]+-\d{8}T\d{6}Z(-\d+)?$"


class Event(_Body):
    """A detection (SPEC §6.4)."""

    TYPE: ClassVar[str] = "event"
    # Stable per device: kind and UTC start, "-2" etc. for a second one in the same second.
    id: str = Field(pattern=EVENT_ID_PATTERN)
    night_id: str = Field(pattern=r"^\d{8}$")
    kind: EventKind
    start: AwareDatetime
    end: AwareDatetime
    confidence: float = Field(ge=0, le=1)
    has_image: bool = False
    data: dict[str, float | int | str | bool | None] = Field(
        default_factory=dict[str, float | int | str | bool | None]
    )

    @model_validator(mode="after")
    def _end_after_start(self) -> Event:
        if self.end < self.start:
            raise ValueError("end is before start")
        if not self.id.startswith(self.kind.value.replace("_", "") + "-"):
            raise ValueError("id must start with the kind")
        return self


def event_id(kind: EventKind, start: datetime, seq: int = 1) -> str:
    """SPEC §6.4: e.g. "meteor-20261008T214512Z", "-2" for a second one in that second."""
    stamp = start.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    base = f"{kind.value.replace('_', '')}-{stamp}"
    return base if seq <= 1 else f"{base}-{seq}"


class ProductKind(StrEnum):
    """Night products (SPEC §5.2)."""

    KEOGRAM = "keogram"
    STARTRAILS = "startrails"
    TIMELAPSE = "timelapse"


PRODUCT_NAMES: dict[ProductKind, tuple[str, str]] = {
    ProductKind.KEOGRAM: ("keogram.jpg", "image/jpeg"),
    ProductKind.STARTRAILS: ("startrails.jpg", "image/jpeg"),
    ProductKind.TIMELAPSE: ("timelapse.mp4", "video/mp4"),
}


class ProductFile(BaseModel):
    """One night product in a `products` message (SPEC §6.3)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: ProductKind
    name: str
    content_type: Literal["image/jpeg", "video/mp4"]
    size: int = Field(ge=0)
    thumbnail: bool
    duration_s: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _name_matches_kind(self) -> ProductFile:
        if (self.name, self.content_type) != PRODUCT_NAMES[self.kind]:
            raise ValueError(f"{self.kind.value} must be {PRODUCT_NAMES[self.kind]}")
        return self


class Products(_Body):
    """The night products of one night (SPEC §6.3)."""

    TYPE: ClassVar[str] = "products"
    night_id: str = Field(pattern=r"^\d{8}$")
    products: list[ProductFile] = Field(min_length=1, max_length=len(PRODUCT_NAMES))


class CommandName(StrEnum):
    """Commands from the hub (SPEC §6.5)."""

    SET_SETTINGS = "set_settings"
    FOCUS_MODE = "focus_mode"
    RESTART = "restart"
    UPDATE = "update"
    UPLOAD_FRAME = "upload_frame"
    UPLOAD_PRODUCT = "upload_product"
    UPLOAD_EVENT = "upload_event"


class Command(_Body):
    """Hub → device command (SPEC §6.5)."""

    TYPE: ClassVar[str] = "command"
    name: CommandName
    args: dict[str, Any] = Field(default_factory=dict[str, Any])


class Ack(_Body):
    """Successful reply to a command (SPEC §6.5)."""

    TYPE: ClassVar[str] = "ack"
    ref: str


class ErrorReply(_Body):
    """Failed reply to a command (SPEC §6.5)."""

    TYPE: ClassVar[str] = "error"
    ref: str
    code: str
    message: str = ""


Body = Hello | Status | FrameInfo | Products | Event | Command | Ack | ErrorReply

_BODY_TYPES: dict[str, type[_Body]] = {
    m.TYPE: m for m in (Hello, Status, FrameInfo, Products, Event, Command, Ack, ErrorReply)
}


class Envelope(BaseModel):
    """Wrapper of every protocol message (SPEC §6.1)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    v: Literal[1] = PROTOCOL_VERSION
    type: str
    id: str = Field(min_length=1, max_length=64)
    ts: AwareDatetime
    body: Body

    @model_validator(mode="before")
    @classmethod
    def _parse_body(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        raw = cast("dict[str, Any]", data)
        body = raw.get("body")
        if not isinstance(body, dict):
            return raw
        model = _BODY_TYPES.get(str(raw.get("type")))
        if model is None:
            raise ValueError(f"unknown message type {raw.get('type')!r}")
        return {**raw, "body": model.model_validate(body)}

    @model_validator(mode="after")
    def _type_matches_body(self) -> Envelope:
        if self.type != self.body.TYPE:
            raise ValueError(f"type {self.type!r} does not match body {self.body.TYPE!r}")
        return self

    @classmethod
    def wrap(cls, body: Body, ts: datetime, msg_id: str | None = None) -> Envelope:
        """Build an envelope for `body`; `ts` must be timezone-aware."""
        return cls(type=body.TYPE, id=msg_id or uuid4().hex, ts=ts, body=body)


def parse_envelope(raw: str | bytes) -> Envelope:
    """Parse a JSON message; raises `pydantic.ValidationError` on invalid input."""
    return Envelope.model_validate_json(raw)
