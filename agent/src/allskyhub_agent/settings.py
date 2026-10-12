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
_PRODUCT_SWITCHES = ("product_keogram", "product_startrails", "product_timelapse")


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
    detect_meteor: str = "normal"
    detect_aurora: str = "normal"
    detect_lightning: str = "normal"
    detect_nlc: str = "normal"
    # Night products and retention (roadmap L9, SPEC §4.5, §5.2).
    keep_days: int = DEFAULT_KEEP_DAYS
    product_keogram: bool = True
    product_startrails: bool = True
    product_timelapse: bool = True
    timelapse_fps: int = 25
    timelapse_quality: str = "normal"

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
                for k in ("keep_days", "timelapse_fps")
                if isinstance(d.get(k), int) and not isinstance(d.get(k), bool)
            }
        )
        later.update({k: d[k] for k in _PRODUCT_SWITCHES if isinstance(d.get(k), bool)})
        if d.get("timelapse_quality") in TIMELAPSE_CRF:
            later["timelapse_quality"] = d["timelapse_quality"]
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
        for k, hi in (("keep_days", 3650), ("timelapse_fps", 60)):
            v = values.get(k)
            if isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= hi:
                extra[k] = v
        for k in _PRODUCT_SWITCHES:
            if isinstance(values.get(k), bool):
                extra[k] = values[k]
        if values.get("timelapse_quality") in TIMELAPSE_CRF:
            extra["timelapse_quality"] = values["timelapse_quality"]
        return replace(s, **extra)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with tmp.open("w") as f:
            json.dump(asdict(self), f)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(path)
