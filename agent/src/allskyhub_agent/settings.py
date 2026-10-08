"""Settings the agent changes itself (setup, SPEC §7.1, §7.3), kept across restarts."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_PATH = Path("/var/lib/allskyhub-agent/settings.json")
DEFAULT_HUB = "https://allskyhub.org"
CAMERA_CHOICES = ("auto", "zwo-asi678mc", "rpi-hq", "sim")


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
        location_ok = (
            isinstance(lat, int | float)
            and isinstance(lon, int | float)
            and -90 <= lat <= 90
            and -180 <= lon <= 180
        )
        return cls().with_updates(
            hub_url=hub if isinstance(hub, str) else None,
            latitude=float(lat) if location_ok and isinstance(lat, int | float) else None,
            longitude=float(lon) if location_ok and isinstance(lon, int | float) else None,
            timezone=tz if isinstance(tz, str) else None,
            camera=cam if isinstance(cam, str) else None,
            day_delay_s=float(dd) if isinstance(dd, int | float) else None,
            night_delay_s=float(nd) if isinstance(nd, int | float) else None,
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with tmp.open("w") as f:
            json.dump(asdict(self), f)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(path)
