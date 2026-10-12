"""Exports for science (roadmap C11): meteor lists and hourly counts, sky brightness.

Plain CSV that others can read without allskyhub: times in UTC (ISO 8601), the camera's
position as the owner configured it (the owner downloads their own data, so unrounded),
directions as compass azimuth and altitude once the camera is oriented (SPEC §6.4).
"""

from __future__ import annotations

import csv
import datetime as dt
import io
from collections import defaultdict
from typing import Any

from allskyhub_server.devices.event_text import shower_name
from allskyhub_server.models import EventRecord, SkySample

# IMO fireball report form; it cannot be prefilled, so the page lists what to enter.
FIREBALL_FORM = "https://fireball.imo.net/members/imo/report_intro"
# Globe at Night takes SQM readings with place and time.
GLOBE_AT_NIGHT = "https://www.globeatnight.org/webapp/"
# A meteor this bright (peak brightness of its track) may be a fireball worth reporting.
FIREBALL_PEAK = 0.9


def _num(value: object) -> str:
    return (
        "" if not isinstance(value, int | float) else f"{float(value):.3f}".rstrip("0").rstrip(".")
    )


def _csv(header: list[str], rows: list[list[object]]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return out.getvalue()


def meteors_csv(events: list[EventRecord], lat: float | None, lon: float | None) -> str:
    rows: list[list[object]] = []
    for e in sorted(events, key=lambda e: e.start):
        if e.kind != "meteor":
            continue
        d: dict[str, Any] = e.data
        shower = d.get("shower")
        rows.append([
            e.start.astimezone(dt.UTC).isoformat(timespec="milliseconds"),
            f"{(e.end - e.start).total_seconds():.2f}",
            _num(d.get("azimuth_deg")),
            _num(d.get("altitude_deg")),
            _num(d.get("direction_deg")),
            d.get("length_px", ""),
            _num(d.get("peak")),
            shower if isinstance(shower, str) else "",
            d.get("shower_match") or "",
            f"{e.confidence:.2f}",
            e.label or "",
            _num(lat),
            _num(lon),
            e.event_id,
        ])  # fmt: skip
    return _csv(
        ["time_utc", "duration_s", "azimuth_deg", "altitude_deg", "image_direction_deg",
         "length_px", "peak", "shower", "shower_match", "confidence", "owner_label",
         "camera_latitude", "camera_longitude", "event_id"],
        rows,
    )  # fmt: skip


def hourly_counts_csv(events: list[EventRecord]) -> str:
    """Meteors per UTC hour: by shower (radiant matches), sporadic, and unassigned."""
    counts: dict[dt.datetime, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    showers: set[str] = set()
    for e in events:
        if e.kind != "meteor" or e.label == "false_positive":
            continue
        hour = e.start.astimezone(dt.UTC).replace(minute=0, second=0, microsecond=0)
        shower = e.data.get("shower")
        if e.data.get("shower_match") != "radiant":
            key = "unassigned"
        elif isinstance(shower, str) and shower:
            key = shower
            showers.add(shower)
        else:
            key = "sporadic"
        counts[hour][key] += 1
    names = sorted(showers)
    rows: list[list[object]] = [
        [hour.isoformat(), sum(c.values()), *[c.get(n, 0) for n in names],
         c.get("sporadic", 0), c.get("unassigned", 0)]
        for hour, c in sorted(counts.items())
    ]  # fmt: skip
    return _csv(["hour_utc", "meteors", *names, "sporadic", "unassigned"], rows)


def best_sqm_per_night(samples: list[SkySample]) -> list[SkySample]:
    best: dict[str, SkySample] = {}
    for s in samples:
        if s.sqm_mag is None:
            continue
        old = best.get(s.night_id)
        if old is None or (old.sqm_mag or 0) < s.sqm_mag:
            best[s.night_id] = s
    return [best[n] for n in sorted(best)]


def sqm_csv(samples: list[SkySample], lat: float | None, lon: float | None) -> str:
    """For Globe at Night and similar: the darkest measurement of each night."""
    rows: list[list[object]] = [
        [s.night_id, s.at.astimezone(dt.UTC).isoformat(timespec="seconds"), _num(s.sqm_mag),
         _num(s.cloud_cover), s.stars if s.stars is not None else "", _num(lat), _num(lon)]
        for s in best_sqm_per_night(samples)
    ]  # fmt: skip
    return _csv(
        ["night", "time_utc", "sqm_mag_arcsec2", "cloud_cover", "stars", "latitude",
         "longitude"],
        rows,
    )  # fmt: skip


def fireball_facts(event: EventRecord) -> list[tuple[str, str]] | None:
    """What the IMO fireball form asks for, for a bright meteor; None for others."""
    if event.kind != "meteor":
        return None
    peak = event.data.get("peak")
    if not isinstance(peak, int | float) or peak < FIREBALL_PEAK:
        return None
    facts = [
        ("Zeit (UTC)", event.start.astimezone(dt.UTC).strftime("%d.%m.%Y %H:%M:%S")),
        ("Dauer", f"{(event.end - event.start).total_seconds():.1f} s".replace(".", ",")),
    ]
    az, alt = event.data.get("azimuth_deg"), event.data.get("altitude_deg")
    if isinstance(az, int | float) and isinstance(alt, int | float):
        facts.append(("Richtung (Mitte der Spur)", f"Azimut {az:.0f}°, Höhe {alt:.0f}°"))
    shower = event.data.get("shower")
    if event.data.get("shower_match") == "radiant":
        facts.append(
            ("Strom", shower_name(shower) if isinstance(shower, str) and shower else "sporadisch")
        )
    return facts
