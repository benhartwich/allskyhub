"""Message models for the device protocol v1 (SPEC §6).

Every message travels in an `Envelope` (SPEC §6.1). The envelope's `type` names the
body model; `Envelope.wrap()` builds one from a body, `parse_envelope()` reads one.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, ClassVar, Literal, cast
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

PROTOCOL_VERSION: Literal[1] = 1


class _Body(BaseModel):
    # SPEC §6.1: receivers ignore fields they do not know, so either side can add one.
    model_config = ConfigDict(extra="ignore", frozen=True)
    TYPE: ClassVar[str]


class Mode(StrEnum):
    """Capture mode (SPEC §4.2)."""

    DAY = "day"
    NIGHT = "night"


class SettingType(StrEnum):
    """Kind of a setting in the settings schema (SPEC §6.9)."""

    NUMBER = "number"
    INT = "int"
    BOOL = "bool"
    CHOICE = "choice"
    STRING = "string"
    LOCATION = "location"  # latitude + longitude, key "location"


_Label = Annotated[str, Field(min_length=1, max_length=60)]
_Group = Annotated[str, Field(min_length=1, max_length=40)]
_Help = Annotated[str, Field(min_length=1, max_length=300)]


class LabelText(BaseModel):
    """A schema text in German and optionally English (SPEC §6.9)."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    de: _Label
    en: _Label | None = None


class GroupText(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    de: _Group
    en: _Group | None = None


class HelpText(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    de: _Help
    en: _Help | None = None


def localized(text: str | LabelText | GroupText | HelpText, lang: str = "de") -> str:
    """A schema text in `lang`, falling back to German (SPEC §6.9)."""
    if isinstance(text, str):
        return text
    return (text.en if lang == "en" else None) or text.de


class SettingChoice(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    value: str = Field(min_length=1, max_length=40)
    label: _Label | LabelText


class SettingSpec(BaseModel):
    """One entry of `hello.settings_schema` (SPEC §6.9)."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    type: SettingType
    label: _Label | LabelText
    group: _Group | GroupText
    help: _Help | HelpText | None = None
    unit: str | None = Field(default=None, max_length=10)
    min: float | None = None
    max: float | None = None
    step: float | None = Field(default=None, gt=0)
    choices: list[SettingChoice] | None = None
    max_length: int | None = Field(default=None, ge=1, le=2000)
    nullable: bool = False
    # A secret (SPEC §6.9): never reported, only `<key>_set`; an empty form field keeps it.
    write_only: bool = False

    @model_validator(mode="after")
    def _fits_type(self) -> SettingSpec:
        numeric = self.type in (SettingType.NUMBER, SettingType.INT)
        if not numeric and (self.min, self.max, self.step) != (None, None, None):
            raise ValueError("min, max and step only for number and int")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("min > max")
        if (self.type is SettingType.CHOICE) != bool(self.choices):
            raise ValueError("choices exactly for choice")
        if self.max_length is not None and self.type is not SettingType.STRING:
            raise ValueError("max_length only for string")
        if self.write_only and self.type is not SettingType.STRING:
            raise ValueError("write_only only for string")
        if (self.type is SettingType.LOCATION) != (self.key == "location"):
            raise ValueError("the location type has the key location")
        return self


def _invalid_value(spec: SettingSpec, v: object) -> bool:
    if v is None:
        return not spec.nullable
    match spec.type:
        case SettingType.BOOL:
            return not isinstance(v, bool)
        case SettingType.INT | SettingType.NUMBER:
            if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v):
                return True
            if spec.type is SettingType.INT and not float(v).is_integer():
                return True
            return (spec.min is not None and v < spec.min) or (
                spec.max is not None and v > spec.max
            )
        case SettingType.CHOICE:
            return v not in {c.value for c in spec.choices or []}
        case SettingType.STRING:
            return not isinstance(v, str) or len(v) > (spec.max_length or 200)
        case SettingType.LOCATION:
            return True  # set through latitude and longitude


def schema_problems(schema: list[SettingSpec]) -> list[str]:
    """Why a settings schema cannot be used (SPEC §6.9); empty if it is fine. The hub
    then keeps the device's previous schema."""
    keys = [s.key for s in schema]
    problems = [f"duplicate key {k}" for k in sorted({k for k in keys if keys.count(k) > 1})]
    problems += [f"{k} is part of location" for k in ("latitude", "longitude") if k in keys]
    return problems


def invalid_settings(schema: list[SettingSpec], values: Mapping[str, object]) -> list[str]:
    """The keys of a partial `set_settings` object the schema does not allow (SPEC §6.9),
    sorted; empty if all are fine."""
    specs = {s.key: s for s in schema}
    bad: set[str] = set()
    if "location" in specs:
        lat, lon = values.get("latitude"), values.get("longitude")
        if ("latitude" in values) != ("longitude" in values):
            bad.update(k for k in ("latitude", "longitude") if k in values)
        elif "latitude" in values:
            for k, v, lim in (("latitude", lat, 90), ("longitude", lon, 180)):
                ok = v is None and specs["location"].nullable
                number = not isinstance(v, bool) and isinstance(v, int | float) and math.isfinite(v)
                ok = ok or (number and -lim <= cast(float, v) <= lim)
                if not ok:
                    bad.add(k)
    for k, v in values.items():
        if k in ("latitude", "longitude") and "location" in specs:
            continue
        spec = specs.get(k)
        if spec is None or _invalid_value(spec, v):
            bad.add(k)
    return sorted(bad)


class Hello(_Body):
    """First message after connecting (SPEC §6.3)."""

    TYPE: ClassVar[str] = "hello"
    device_id: str = Field(pattern=r"^[a-z2-7]{26}$")  # SPEC §6.2
    profile: str
    agent_version: str
    capabilities: list[str] = Field(default_factory=list[str])
    # SPEC §6.9: the settings form for web, app and local UI; empty for old agents.
    settings_schema: list[SettingSpec] = Field(default_factory=list[SettingSpec])


class DeviceSettings(BaseModel):
    """The device's current settings, reported in `status` (SPEC §6.3). Further keys from
    the settings schema (SPEC §6.9) come as extra fields, JSON scalars only."""

    model_config = ConfigDict(extra="allow", frozen=True)
    __pydantic_extra__: dict[str, str | int | float | bool | None] = Field(  # pyright: ignore[reportIncompatibleVariableOverride]
        init=False
    )

    latitude: float | None = None
    longitude: float | None = None
    timezone: str
    camera: str
    day_delay_s: float = Field(ge=0)
    night_delay_s: float = Field(ge=0)


class SkyMetrics(BaseModel):
    """Sky condition measured on the newest frame, reported in `status` (SPEC §6.3)."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    at: AwareDatetime  # captured_at of the measured frame
    night_id: str = Field(pattern=r"^\d{8}$")  # SPEC §4.5
    cloud_cover: float | None = Field(default=None, ge=0, le=1)
    sqm_mag: float | None = None  # mag/arcsec², approximate until calibrated
    stars: int | None = Field(default=None, ge=0)
    # SPEC §6.3: cloud cover per sky cell (8 azimuth sectors x 3 altitude bands, index =
    # 8·band + sector), each 0..1 or null; null unless the camera is oriented and it is night.
    cloud_map: list[Annotated[float, Field(ge=0, le=1)] | None] | None = Field(
        default=None, min_length=24, max_length=24
    )


class Lens(BaseModel):
    """The fitted lens of a plate solve (SPEC §4.8, §6.3), in pixels of a width x height
    frame: r = a1·t + a3·t³ with t = zenith angle / 90°, centred on (cx, cy), after the
    tilt of the camera axis toward east and north. No bounds on purpose: a bad value must
    not cost the whole status; users check plausibility (width/height 0 = unknown)."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    cx: float
    cy: float
    a1: float
    a3: float
    tilt_east_deg: float
    tilt_north_deg: float
    width: int
    height: int


class Orientation(BaseModel):
    """The image's orientation from a plate solve, reported in `status` (SPEC §6.3, §4.8)."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    north_deg: float = Field(ge=0, lt=360)  # image direction of north, 0 = up, clockwise
    mirrored: bool  # east counter-clockwise from north (the usual view of a sky camera)
    solved_at: AwareDatetime
    stars: int = Field(ge=0)
    rms_deg: float = Field(ge=0)
    lens: Lens | None = None  # optional, for drawing the sky onto the image


class ObservatorySequence(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    name: str | None = Field(default=None, max_length=200)
    progress: float = Field(ge=0, le=1)


class ObservatoryImage(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    at: AwareDatetime | None = None
    filter: str | None = Field(default=None, max_length=40)
    exposure_s: float | None = Field(default=None, ge=0)
    hfr: float | None = Field(default=None, ge=0)
    stars: int | None = Field(default=None, ge=0)


class Observatory(BaseModel):
    """What the telescope next to the camera does, relayed from N.I.N.A. (SPEC §6.3, C15).
    Metadata only, never images."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    source: str = Field(default="nina", max_length=20)
    at: AwareDatetime
    connected: bool
    target: str | None = Field(default=None, max_length=200)
    sequence: ObservatorySequence | None = None
    last_image: ObservatoryImage | None = None
    safe: bool | None = None


class AdsbStatus(BaseModel):
    """The owner's ADS-B receiver (SPEC §6.3, §7.8)."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    at: AwareDatetime | None = None  # when it was last read
    aircraft: int = Field(default=0, ge=0)
    error: str | None = Field(default=None, max_length=40)  # unreachable, bad_data


class UpdateState(StrEnum):
    """The updater's last state (SPEC §8)."""

    UP_TO_DATE = "up_to_date"
    DOWNLOADING = "downloading"
    WAITING = "waiting"  # downloaded, waits for the day
    INSTALLING = "installing"
    INSTALLED = "installed"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


class UpdateStatus(BaseModel):
    """Reported in `status` (SPEC §6.3, §8)."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    state: UpdateState
    version: str | None = None  # the version the state is about
    at: AwareDatetime  # when the updater reached this state
    code: str | None = None  # why it failed or rolled back, e.g. "unhealthy", "checksum"


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
    settings: DeviceSettings | None = None
    sky: SkyMetrics | None = None
    orientation: Orientation | None = None
    update: UpdateStatus | None = None
    # SPEC §6.3: N.I.N.A.'s state (roadmap C15), null unless configured.
    observatory: Observatory | None = None
    # SPEC §7.5: why the Alpaca server does not run although it is switched on.
    alpaca_error: str | None = Field(default=None, max_length=40)
    # SPEC §4.1: the owner paused capture (cleaning the dome); no warnings meanwhile.
    capture_paused: bool = False
    # SPEC §7.8: the owner's ADS-B receiver, null unless `adsb_url` is set.
    adsb: AdsbStatus | None = None


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
    # SPEC §4.9: a bad frame (too dark or too bright), left out of products and grids.
    bad: Literal["dark", "bright"] | None = None


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
    # SPEC §6.4: taken over from another program's records (roadmap L18), never a push.
    imported: bool = False
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
    """Night and day products (SPEC §5.2)."""

    KEOGRAM = "keogram"
    STARTRAILS = "startrails"
    TIMELAPSE = "timelapse"
    DAY_TIMELAPSE = "day_timelapse"
    DAY_KEOGRAM = "day_keogram"


PRODUCT_NAMES: dict[ProductKind, tuple[str, str]] = {
    ProductKind.KEOGRAM: ("keogram.jpg", "image/jpeg"),
    ProductKind.STARTRAILS: ("startrails.jpg", "image/jpeg"),
    ProductKind.TIMELAPSE: ("timelapse.mp4", "video/mp4"),
    ProductKind.DAY_TIMELAPSE: ("day-timelapse.mp4", "video/mp4"),
    ProductKind.DAY_KEOGRAM: ("day-keogram.jpg", "image/jpeg"),
}


class ProductFile(BaseModel):
    """One night product in a `products` message (SPEC §6.3)."""

    model_config = ConfigDict(extra="ignore", frozen=True)

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


# Upper bound of a `products` list, with room for kinds a newer agent adds.
MAX_PRODUCTS = 20


class Products(_Body):
    """The night products of one night (SPEC §6.3)."""

    TYPE: ClassVar[str] = "products"
    night_id: str = Field(pattern=r"^\d{8}$")
    products: list[ProductFile] = Field(max_length=MAX_PRODUCTS)

    @field_validator("products", mode="before")
    @classmethod
    def _skip_unknown_kinds(cls, value: object) -> object:
        """SPEC §6.1: entries of a kind this side does not know yet are skipped, so a
        newer agent's products never make an older hub refuse the whole message."""
        if not isinstance(value, list):
            return value
        items = cast(list[object], value)
        if not items:
            raise ValueError("at least one product")
        known = {k.value for k in ProductKind}
        kept: list[object] = []
        for item in items:
            entry: object = item
            if isinstance(entry, dict) and cast(dict[str, object], entry).get("kind") not in known:
                continue
            kept.append(item)
        return kept


class CommandName(StrEnum):
    """Commands from the hub (SPEC §6.5)."""

    SET_SETTINGS = "set_settings"
    FOCUS_MODE = "focus_mode"
    RESTART = "restart"
    UPDATE = "update"
    UPLOAD_FRAME = "upload_frame"
    UPLOAD_PRODUCT = "upload_product"
    UPLOAD_EVENT = "upload_event"
    LOCAL_ACCESS = "local_access"
    LABEL_EVENT = "label_event"


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


LabelName = Literal["confirmed", "false_positive"]
LabelReason = Literal["aircraft", "satellite", "artifact", "other"]


class EventLabel(_Body):
    """A label of a detection, set on the camera (SPEC §6.4 Labels); the same fields are
    the `args` of the `label_event` command (SPEC §6.5). `auto` labels come from a
    matcher (aircraft, SPEC §7.8), never replace a label and give way to the owner's."""

    TYPE: ClassVar[str] = "event_label"
    night_id: str = Field(pattern=r"^\d{8}$")
    event_id: str = Field(pattern=EVENT_ID_PATTERN)
    label: LabelName | None = None  # null removes the label
    reason: LabelReason | None = None  # only with false_positive
    at: AwareDatetime
    auto: bool = False
    detail: str | None = Field(default=None, max_length=100)  # only with auto

    @model_validator(mode="after")
    def _reason_only_for_false_positive(self) -> EventLabel:
        if self.reason is not None and self.label != "false_positive":
            raise ValueError("reason only with false_positive")
        if self.detail is not None and not self.auto:
            raise ValueError("detail only with auto")
        return self

    def replaces(self, old: EventLabel | None) -> bool:
        """Whether this label wins over the stored one (SPEC §6.4 Labels)."""
        if old is None:
            return True
        if self.auto:
            return False
        return old.auto or self.at > old.at


class AircraftPosition(BaseModel):
    """One aircraft seen by the owner's receiver (SPEC §6.3 `aircraft`)."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    hex: str = Field(pattern=r"^[0-9a-f]{6}$")
    flight: str | None = Field(default=None, max_length=8)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    alt_m: float | None = None
    track_deg: float | None = Field(default=None, ge=0, lt=360)
    speed_kn: float | None = Field(default=None, ge=0)
    seen_s: float = Field(ge=0, le=60)


class Aircraft(_Body):
    """The aircraft the owner's ADS-B receiver sees (SPEC §6.3, §7.8)."""

    TYPE: ClassVar[str] = "aircraft"
    at: AwareDatetime
    aircraft: list[AircraftPosition] = Field(default_factory=list[AircraftPosition], max_length=200)


class NightDeleted(_Body):
    """The owner deleted a whole night on the camera (SPEC §6.3, §7)."""

    TYPE: ClassVar[str] = "night_deleted"
    night_id: str = Field(pattern=r"^\d{8}$")


Body = (
    Hello
    | Status
    | FrameInfo
    | Products
    | Event
    | Command
    | Ack
    | ErrorReply
    | EventLabel
    | NightDeleted
    | Aircraft
)

_BODY_TYPES: dict[str, type[_Body]] = {
    m.TYPE: m
    for m in (
        Hello, Status, FrameInfo, Products, Event, Command, Ack, ErrorReply, EventLabel,
        NightDeleted, Aircraft,
    )
}  # fmt: skip


class Envelope(BaseModel):
    """Wrapper of every protocol message (SPEC §6.1)."""

    model_config = ConfigDict(extra="ignore", frozen=True)

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
