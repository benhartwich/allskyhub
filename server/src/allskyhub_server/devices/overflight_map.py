"""The night's overflights on a sky map (SPEC §6.4 Overflights, roadmap L29), with the
possible meteors of the night as their own kind (Benjamin, 2026-10-15).

A polar map of the whole sky as seen looking up: the zenith in the middle, the horizon as
the circle, north at the top and east on the left. Each overflight is a line along the
great circle from its start to its end, drawn in SVG on the hub; ``overflights.js`` plays
the night like a timelapse. Constellations come from ``allskyhub_sky`` once an hour, so
they turn with the sky as the night goes on. The aircraft by day (SPEC §7.8 By day) get a
map of their own, without constellations (Benjamin, 2026-10-19).
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from allskyhub_server.devices import events
from allskyhub_server.devices.event_text import shower_name
from allskyhub_server.models import EventRecord
from allskyhub_sky import sky_lines

if TYPE_CHECKING:
    from allskyhub_server.models import AircraftPass

SIZE = 440
CENTRE = SIZE / 2
RADIUS = 200.0
STEPS = 16


def xy(alt: float, az: float) -> tuple[float, float]:
    """A direction on the map: distance from the centre linear in zenith angle."""
    r = RADIUS * (90.0 - max(0.0, min(90.0, alt))) / 90.0
    a = math.radians(az)
    return round(CENTRE - r * math.sin(a), 1), round(CENTRE - r * math.cos(a), 1)


def _vector(alt: float, az: float) -> tuple[float, float, float]:
    a, z = math.radians(alt), math.radians(az)
    return math.cos(a) * math.sin(z), math.cos(a) * math.cos(z), math.sin(a)


def great_circle(
    start: tuple[float, float], end: tuple[float, float], steps: int = STEPS
) -> list[tuple[float, float]]:
    """``steps + 1`` directions (alt, az) from ``start`` to ``end`` along the sky."""
    p, q = _vector(*start), _vector(*end)
    dot = max(-1.0, min(1.0, sum(a * b for a, b in zip(p, q, strict=True))))
    omega = math.acos(dot)
    out: list[tuple[float, float]] = []
    for i in range(steps + 1):
        t = i / steps
        s = math.sin(omega)
        if s < 1e-6:  # the same point, or opposite ones (no single great circle)
            v = p if omega < 1.0 else (p[0] * (1 - 2 * t), p[1] * (1 - 2 * t), 1.0 - abs(1 - 2 * t))
        else:
            f, g = math.sin((1 - t) * omega) / s, math.sin(t * omega) / s
            v = (f * p[0] + g * q[0], f * p[1] + g * q[1], f * p[2] + g * q[2])
        alt = math.degrees(math.asin(max(-1.0, min(1.0, v[2]))))
        az = math.degrees(math.atan2(v[0], v[1])) % 360.0
        out.append((alt, az))
    return out


def _path(points: list[tuple[float, float]]) -> str:
    return "M" + " L".join(f"{x} {y}" for x, y in (xy(alt, az) for alt, az in points))


def _ends(event: EventRecord) -> tuple[tuple[float, float], tuple[float, float]] | None:
    data: dict[str, Any] = event.data or {}
    values = [data.get(k) for k in (
        "start_altitude_deg", "start_azimuth_deg", "end_altitude_deg", "end_azimuth_deg",
    )]  # fmt: skip
    if not all(isinstance(v, int | float) for v in values):
        return None
    a1, z1, a2, z2 = (float(v) for v in values)  # pyright: ignore[reportArgumentType]
    return (a1, z1), (a2, z2)


@dataclass(frozen=True)
class Track:
    event: EventRecord
    group: str  # satellite, aircraft, unknown, meteor
    name: str | None
    path: str | None  # SVG path on the map, None without an orientation
    head: tuple[float, float] | None  # where it ended, for the arrow dot
    text: str | None  # "NW 15° → SO 31°"


@dataclass(frozen=True)
class Line:
    """One line on a map, the same for the night's tracks and the passes by day."""

    group: str
    start: dt.datetime
    end: dt.datetime
    name: str | None
    path: str
    head: tuple[float, float]


def lines(mapped: list[Track]) -> list[Line]:
    """The night's tracks that have a path, in the same order."""
    return [
        Line(t.group, t.event.start, t.event.end, t.name, t.path, t.head)
        for t in mapped
        if t.path is not None and t.head is not None
    ]


def day_lines(passes: list[AircraftPass]) -> list[Line]:
    """SPEC §7.8 By day: each pass from its start through its highest point to its end."""
    out: list[Line] = []
    for p in passes:
        start = (p.start_alt_deg, p.start_az_deg)
        top = (p.top_alt_deg, p.top_az_deg)
        end = (p.end_alt_deg, p.end_az_deg)
        points = great_circle(start, top, STEPS // 2) + great_circle(top, end, STEPS // 2)[1:]
        out.append(
            Line("aircraft", p.start, p.end, p.callsign or p.icao.upper(), _path(points), xy(*end))
        )
    return out


@dataclass(frozen=True)
class Sky:
    at: dt.datetime
    lines: list[str]
    names: list[tuple[str, float, float]]


def _shower(event: EventRecord) -> str | None:
    shower = (event.data or {}).get("shower")
    return shower_name(shower) if isinstance(shower, str) and shower else None


def tracks(rows: list[EventRecord]) -> list[Track]:
    out: list[Track] = []
    for e in rows:
        group = events.overflight(e)
        if group is None:
            # Possible meteors (not dismissed) as their own kind, for comparison.
            if e.kind != "meteor" or events.dismissed(e):
                continue
            group = "meteor"
        ends = _ends(e)
        out.append(
            Track(
                e, group, events.overflight_name(e) if group != "meteor" else _shower(e),
                _path(great_circle(*ends)) if ends else None,
                xy(*ends[1]) if ends else None,
                events.overflight_path(e),
            )
        )  # fmt: skip
    return out


def skies(place: tuple[float, float] | None, start: dt.datetime, end: dt.datetime) -> list[Sky]:
    """Constellations once an hour from ``start`` to ``end`` (at least one)."""
    if place is None:
        return []
    first = start.replace(minute=0, second=0, microsecond=0)
    hours = max(1, math.ceil((end - first).total_seconds() / 3600) + 1)
    out: list[Sky] = []
    for h in range(min(hours, 16)):
        at = first + dt.timedelta(hours=h)
        found = sky_lines(*place, at, min_alt=0.0)
        out.append(
            Sky(
                at,
                [_path(line) for line in found.lines],
                [(name, *xy(alt, az)) for name, alt, az in found.names if alt > 8],
            )
        )
    return out


def rings() -> list[tuple[int, float]]:
    """Altitude circles at 30° and 60°: (altitude, radius)."""
    return [(alt, RADIUS * (90 - alt) / 90) for alt in (30, 60)]


def compass() -> list[tuple[str, float, float]]:
    """N, O, S, W just outside the horizon."""
    out: list[tuple[str, float, float]] = []
    for name, az in (("N", 0.0), ("O", 90.0), ("S", 180.0), ("W", 270.0)):
        a = math.radians(az)
        r = RADIUS + 13
        out.append(
            (name, round(CENTRE - r * math.sin(a), 1), round(CENTRE - r * math.cos(a) + 5, 1))
        )
    return out
