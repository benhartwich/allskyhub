"""„Ziel frei?“ (roadmap H4): is an object clear now and in the next hours at a camera?

Where the object stands (``allskyhub_sky``), how cloudy the forecast is for each hour, how
dark it is and where the moon is, and, for now, what the camera itself sees: its cloud
cover for the whole sky (SPEC §6.3 ``status.sky``). Once the camera reports cover per
sky area, the cell in the object's direction takes its place.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field
from typing import Any, Literal

from allskyhub_server.devices.tonight import Hour, zone_for
from allskyhub_sky import alt_az, precess, sidereal_deg
from allskyhub_sky.astro import moon_altitude, moon_illumination, moon_radec, sun_altitude
from allskyhub_sky.objects import SkyObject, catalogue, radec

HOURS = 12
MIN_ALT = 20.0  # lower, the air and the horizon's haze swallow most objects
DARK_SUN = -12.0  # nautical twilight: dark enough for the brighter objects
CLEAR = 30  # % forecast cloud cover
PARTLY = 60
MOON_NEAR = 15.0  # degrees
MOON_BRIGHT = 0.5
NOW_FRESH = dt.timedelta(minutes=20)
COMPASS = ("N", "NO", "O", "SO", "S", "SW", "W", "NW")

Verdict = Literal["clear", "partly", "cloudy", "low", "bright"]
VERDICT_TEXT: dict[str, str] = {
    "clear": "frei",
    "partly": "teils bewölkt",
    "cloudy": "bewölkt",
    "low": "zu tief",
    "bright": "zu hell",
}


@dataclass(frozen=True)
class TargetHour:
    at: dt.datetime
    alt: float
    az: float
    clouds: int | None
    sun_alt: float
    moon_near: bool
    verdict: Verdict

    @property
    def direction(self) -> str:
        return COMPASS[round(self.az / 45.0) % 8]


@dataclass(frozen=True)
class TargetNow:
    alt: float
    az: float
    # The camera's cloud cover (0..1) and where it comes from, or None (no fresh value).
    cloud_cover: float | None
    source: str
    verdict: Verdict

    @property
    def direction(self) -> str:
        return COMPASS[round(self.az / 45.0) % 8]


@dataclass
class Plan:
    target: SkyObject
    now: TargetNow
    hours: list[TargetHour] = field(default_factory=lambda: list[TargetHour]())
    tz: str = "UTC"

    @property
    def best(self) -> tuple[dt.datetime, dt.datetime] | None:
        """The longest run of good hours."""
        runs: list[list[TargetHour]] = []
        for h in self.hours:
            if h.verdict == "clear":
                if runs and runs[-1][-1].at + dt.timedelta(hours=1) == h.at:
                    runs[-1].append(h)
                else:
                    runs.append([h])
        if not runs:
            return None
        run = max(runs, key=len)
        return run[0].at, run[-1].at + dt.timedelta(hours=1)

    def clock(self, at: dt.datetime) -> str:
        """Time at the camera."""
        return at.astimezone(zone_for(self.tz)).strftime("%H:%M")

    def summary(self) -> str:
        best = self.best
        if self.now.verdict == "clear":
            return f"{self.target.label} ist jetzt frei."
        if best is None:
            return f"{self.target.label}: in den nächsten {HOURS} Stunden keine gute Zeit."
        start, end = (self.clock(t) for t in best)
        return f"{self.target.label}: am besten von {start} bis {end} Uhr."


def find(key: str) -> SkyObject | None:
    return next((o for o in catalogue() if o.key.lower() == key.strip().lower()), None)


def _sep(a: tuple[float, float], b: tuple[float, float]) -> float:
    ra1, d1, ra2, d2 = (math.radians(v) for v in (*a, *b))
    c = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(ra1 - ra2)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def _position(key: str, at: dt.datetime, lat: float, lon: float) -> tuple[float, float, float]:
    """Altitude, azimuth and the distance to the moon (degrees)."""
    found = radec(key, at)
    if found is None:
        raise ValueError(f"unknown object {key!r}")
    ra, dec = precess(*found, at)
    alt, az = alt_az(ra, dec, sidereal_deg(at, lon), lat)
    return alt, az, _sep((ra, dec), moon_radec(at))


def _judge(alt: float, sun: float, clouds: float | None) -> Verdict:
    if alt < MIN_ALT:
        return "low"
    if sun > DARK_SUN:
        return "bright"
    if clouds is None or clouds <= CLEAR:
        return "clear"
    return "partly" if clouds <= PARTLY else "cloudy"


def plan(
    target: SkyObject,
    status: dict[str, Any] | None,
    forecast: list[Hour],
    now: dt.datetime,
) -> Plan | None:
    """None when the camera's location is unknown."""
    settings: dict[str, Any] = (status or {}).get("settings") or {}
    lat, lon = settings.get("latitude"), settings.get("longitude")
    if not isinstance(lat, int | float) or not isinstance(lon, int | float):
        return None
    lat, lon = float(lat), float(lon)
    clouds = {h.at: h.clouds for h in forecast}

    alt, az, _ = _position(target.key, now, lat, lon)
    sky: dict[str, Any] = (status or {}).get("sky") or {}
    cover: float | None = None
    try:
        measured = dt.datetime.fromisoformat(str(sky.get("at")))
    except ValueError:
        measured = None
    value = sky.get("cloud_cover")
    if measured is not None and now - measured <= NOW_FRESH and isinstance(value, int | float):
        cover = float(value)
    now_verdict = _judge(
        alt,
        sun_altitude(now, lat, lon),
        cover * 100 if cover is not None else clouds.get(_hour(now)),
    )
    result = Plan(
        target,
        TargetNow(alt, az, cover, "ganzer Himmel", now_verdict),
        tz=str(settings.get("timezone") or "UTC"),
    )
    moon_lit = moon_illumination(now) >= MOON_BRIGHT
    start = _hour(now) + dt.timedelta(hours=1)
    for i in range(HOURS):
        at = start + dt.timedelta(hours=i)
        h_alt, h_az, moon_sep = _position(target.key, at, lat, lon)
        sun = sun_altitude(at, lat, lon)
        moon_near = moon_lit and moon_sep < MOON_NEAR and moon_altitude(at, lat, lon) > 0
        cover_at = clouds.get(at)
        verdict = _judge(h_alt, sun, cover_at)
        result.hours.append(TargetHour(at, h_alt, h_az, cover_at, sun, moon_near, verdict))
    return result


def _hour(t: dt.datetime) -> dt.datetime:
    return t.astimezone(dt.UTC).replace(minute=0, second=0, microsecond=0)
