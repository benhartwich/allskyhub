"""Settings the agent changes itself (setup, SPEC §7.1, §7.3), kept across restarts."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_PATH = Path("/var/lib/allskyhub-agent/settings.json")
DEFAULT_HUB = "https://allskyhub.org"
CAMERA_CHOICES = ("auto", "zwo-asi678mc", "rpi-hq", "sim")
# Per detector (roadmap L8): off, or how readily it reports (detect/tuning.py).
DETECTORS = ("meteor", "aurora", "lightning", "nlc")
DETECT_CHOICES = ("off", "low", "normal", "high")
# Night products (roadmap L9): x264 CRF per quality.
TIMELAPSE_CRF = {"small": 28, "normal": 23, "high": 19}
DEFAULT_KEEP_DAYS = 14
_PRODUCT_SWITCHES = (
    "product_keogram",
    "product_startrails",
    "product_timelapse",
    "product_day_timelapse",
    "product_day_keogram",
    "product_mini_timelapse",
    "alpaca_enabled",
    "bad_frames",
    "moon_mode",
    "mqtt_tls",
)
# MQTT (SPEC §7.7, roadmap L23): free text, checked by `valid_mqtt`.
_MQTT_STRINGS = ("mqtt_host", "mqtt_user", "mqtt_prefix")
# Whole numbers the schema bounds (settings_schema.py); load() and with_values() keep them.
_INT_RANGES = {
    "keep_days": (1, 3650),
    "timelapse_fps": (1, 60),
    "alpaca_port": (1024, 65535),
    "safe_cloud_max": (5, 95),
    "safe_hold_min": (0, 120),
    "bad_dark_pct": (0, 20),
    "bad_bright_pct": (50, 100),
    "mqtt_port": (1, 65535),
}


def valid_nina_url(url: str) -> bool:
    """Empty (off) or an http(s) address with a host (SPEC §7.5)."""
    u = url.strip()
    if not u:
        return True
    head, sep, rest = u.partition("://")
    return sep == "://" and head in ("http", "https") and bool(rest.split("/", 1)[0])


def valid_mqtt(key: str, value: str) -> bool:
    """SPEC §7.7: a host name or address without scheme, a topic prefix without MQTT
    wildcards; the user is free text."""
    v = value.strip()
    if key == "mqtt_host":
        return v == "" or ("://" not in v and " " not in v and "/" not in v and len(v) <= 253)
    if key == "mqtt_prefix":
        return (
            0 < len(v) <= 40
            and not any(c in v for c in "#+ \0")
            and not v.startswith("/")
            and not v.endswith("/")
        )
    return len(v) <= 100


def valid_timezone(name: str) -> bool:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


@dataclass(frozen=True)
class AgentSettings:
    hub_url: str = DEFAULT_HUB
    latitude: float | None = None
    longitude: float | None = None
    timezone: str = "UTC"
    # "auto": a ZWO camera if one is connected, else a Raspberry Pi camera (SPEC §3).
    camera: str = "auto"
    # Pause between frames (SPEC §4.1).
    day_delay_s: float = 30.0
    night_delay_s: float = 0.0
    # Settings from the local UI and the hub's form (SPEC §6.9, roadmap L7). None: the
    # hardware profile's or the camera's own value.
    night_max_exposure_s: float | None = None
    night_max_gain: int | None = None
    wb_r: int | None = None
    wb_b: int | None = None
    jpeg_quality: int = 90
    # Moon mode (SPEC §4.3, roadmap L20) and bad frames (SPEC §4.9, roadmap L19).
    moon_mode: bool = True
    bad_frames: bool = True
    bad_dark_pct: int = 2
    bad_bright_pct: int = 90
    detect_meteor: str = "normal"
    detect_aurora: str = "normal"
    detect_lightning: str = "normal"
    detect_nlc: str = "normal"
    # Night products and retention (roadmap L9, SPEC §4.5, §5.2).
    keep_days: int = DEFAULT_KEEP_DAYS
    product_keogram: bool = True
    product_startrails: bool = True
    product_timelapse: bool = True
    # Day products and the mini timelapse (roadmap L21, SPEC §5.2).
    product_day_timelapse: bool = True
    product_day_keogram: bool = False
    product_mini_timelapse: bool = False
    timelapse_fps: int = 25
    timelapse_quality: str = "normal"
    # ASCOM Alpaca for observatory software (SPEC §7.5, roadmap L16).
    alpaca_enabled: bool = False
    alpaca_port: int = 11111
    safe_cloud_max: int = 30  # %
    safe_hold_min: int = 10
    nina_url: str = ""  # N.I.N.A.'s Advanced API, e.g. http://192.168.1.30:1888; "" = off
    # MQTT for home automation (SPEC §7.7, roadmap L23); "" = off.
    mqtt_host: str = ""
    mqtt_port: int = 1883
    mqtt_tls: bool = False
    mqtt_user: str = ""
    mqtt_password: str | None = None  # write_only (SPEC §6.9)
    mqtt_prefix: str = "allskyhub"

    @property
    def has_location(self) -> bool:
        return self.latitude is not None and self.longitude is not None

    def with_updates(
        self,
        hub_url: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        timezone: str | None = None,
        camera: str | None = None,
        day_delay_s: float | None = None,
        night_delay_s: float | None = None,
    ) -> AgentSettings:
        """A copy with every given value replaced; None keeps the current one."""
        s = self
        if hub_url:
            s = replace(s, hub_url=hub_url)
        if latitude is not None and longitude is not None:
            s = replace(s, latitude=latitude, longitude=longitude)
        if timezone and valid_timezone(timezone):
            s = replace(s, timezone=timezone)
        if camera in CAMERA_CHOICES:
            s = replace(s, camera=camera)
        if day_delay_s is not None and 0 <= day_delay_s <= 3600:
            s = replace(s, day_delay_s=float(day_delay_s))
        if night_delay_s is not None and 0 <= night_delay_s <= 3600:
            s = replace(s, night_delay_s=float(night_delay_s))
        return s

    @classmethod
    def load(cls, path: Path) -> AgentSettings:
        try:
            data: object = json.loads(path.read_text())
        except (OSError, ValueError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        d = cast("dict[str, object]", data)
        hub, lat, lon, tz, cam = (
            d.get("hub_url"),
            d.get("latitude"),
            d.get("longitude"),
            d.get("timezone"),
            d.get("camera"),
        )
        dd, nd = d.get("day_delay_s"), d.get("night_delay_s")
        later = {
            k: d[k]
            for k in ("night_max_exposure_s", "night_max_gain", "wb_r", "wb_b", "jpeg_quality")
            if isinstance(d.get(k), int | float) and not isinstance(d.get(k), bool)
        }
        detect = {
            f"detect_{k}": d[f"detect_{k}"]
            for k in DETECTORS
            if d.get(f"detect_{k}") in DETECT_CHOICES
        }
        later.update(detect)
        later.update(
            {
                k: d[k]
                for k in _INT_RANGES
                if isinstance(d.get(k), int) and not isinstance(d.get(k), bool)
            }
        )
        later.update({k: d[k] for k in _PRODUCT_SWITCHES if isinstance(d.get(k), bool)})
        if d.get("timelapse_quality") in TIMELAPSE_CRF:
            later["timelapse_quality"] = d["timelapse_quality"]
        if isinstance(d.get("nina_url"), str):
            later["nina_url"] = d["nina_url"]
        for k in _MQTT_STRINGS:
            v = d.get(k)
            if isinstance(v, str) and valid_mqtt(k, v):
                later[k] = v
        if isinstance(d.get("mqtt_password"), str):
            later["mqtt_password"] = d["mqtt_password"]
        location_ok = (
            isinstance(lat, int | float)
            and isinstance(lon, int | float)
            and -90 <= lat <= 90
            and -180 <= lon <= 180
        )
        return (
            cls()
            .with_values(later)
            .with_updates(
                hub_url=hub if isinstance(hub, str) else None,
                latitude=float(lat) if location_ok and isinstance(lat, int | float) else None,
                longitude=float(lon) if location_ok and isinstance(lon, int | float) else None,
                timezone=tz if isinstance(tz, str) else None,
                camera=cam if isinstance(cam, str) else None,
                day_delay_s=float(dd) if isinstance(dd, int | float) else None,
                night_delay_s=float(nd) if isinstance(nd, int | float) else None,
            )
        )

    def with_values(self, values: Mapping[str, Any]) -> AgentSettings:
        """A copy with the keys in `values` set; `None` resets a nullable setting (SPEC
        §6.5). The caller has checked them against the settings schema (SPEC §6.9)."""
        s = self.with_updates(
            latitude=values.get("latitude"),
            longitude=values.get("longitude"),
            timezone=values.get("timezone"),
            camera=values.get("camera"),
            day_delay_s=values.get("day_delay_s"),
            night_delay_s=values.get("night_delay_s"),
        )
        extra: dict[str, Any] = {}
        for key in ("night_max_exposure_s", "night_max_gain", "wb_r", "wb_b"):
            if key in values:
                v = values[key]
                extra[key] = None if v is None else (float(v) if key.endswith("_s") else int(v))
        if values.get("jpeg_quality") is not None:
            extra["jpeg_quality"] = int(values["jpeg_quality"])
        for k in DETECTORS:
            if values.get(f"detect_{k}") in DETECT_CHOICES:
                extra[f"detect_{k}"] = values[f"detect_{k}"]
        for k, (lo, hi) in _INT_RANGES.items():
            v = values.get(k)
            if isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi:
                extra[k] = v
        for k in _PRODUCT_SWITCHES:
            if isinstance(values.get(k), bool):
                extra[k] = values[k]
        if values.get("timelapse_quality") in TIMELAPSE_CRF:
            extra["timelapse_quality"] = values["timelapse_quality"]
        if isinstance(values.get("nina_url"), str) and valid_nina_url(values["nina_url"]):
            extra["nina_url"] = values["nina_url"].strip()
        for k in _MQTT_STRINGS:
            v = values.get(k)
            if isinstance(v, str) and valid_mqtt(k, v):
                extra[k] = v.strip()
        if "mqtt_password" in values:
            pw = values["mqtt_password"]
            if pw is None or (isinstance(pw, str) and pw):
                extra["mqtt_password"] = pw  # None deletes, "" keeps (SPEC §6.9)
        return replace(s, **extra)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        # Only the agent may read it: it can hold the MQTT password (SPEC §7.7).
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(asdict(self), f)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(path)
