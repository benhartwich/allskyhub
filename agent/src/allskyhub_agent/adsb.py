"""Aircraft from the owner's own ADS-B receiver (SPEC §7.8, roadmap H3).

With `adsb_url` set the agent reads the receiver's `aircraft.json` (readsb, dump1090-fa,
tar1090) every 5 s on the local network, keeps the last 10 minutes of positions, relays
the current ones to the hub as `aircraft` every 10 s and checks every new meteor against
them: when both ends of its track lie within 2° of an aircraft's apparent path, the meteor
gets `data.aircraft` and an automatic label `false_positive`/`aircraft`. It only reads; a
receiver that does not answer is retried and never slows capture.
"""

from __future__ import annotations

import itertools
import logging
import math
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx

from allskyhub_protocol import AdsbStatus, Aircraft, AircraftPosition, Event, EventKind

log = logging.getLogger(__name__)

POLL_S = 5.0
RELAY_S = 10.0
KEEP = timedelta(minutes=10)
RANGE_KM = 150.0
MAX_AIRCRAFT = 200
MAX_AGE_S = 60.0
# Matching (SPEC §7.8): ADS-B latency and exposure, tolerance, minimum height.
SLACK = timedelta(seconds=30)
TOLERANCE_DEG = 2.0
MIN_ALT_DEG = 5.0
STEP = timedelta(seconds=1)
MAX_GAP = timedelta(seconds=30)  # do not draw a path across a longer gap
_FT = 0.3048
_EARTH_KM = 6371.0


def _distance_bearing(lat: float, lon: float, lat2: float, lon2: float) -> tuple[float, float]:
    """Great-circle distance (km) and bearing (degrees from north over east)."""
    p1, p2 = math.radians(lat), math.radians(lat2)
    dl = math.radians(lon2 - lon)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    d = 2 * _EARTH_KM * math.asin(min(1.0, math.sqrt(a)))
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return d, math.degrees(math.atan2(y, x)) % 360.0


def apparent(
    lat: float, lon: float, a_lat: float, a_lon: float, alt_m: float
) -> tuple[float, float]:
    """(altitude, azimuth) in degrees of an aircraft seen from the camera, the camera at sea
    level (SPEC §7.8), with the earth's curvature."""
    d_km, az = _distance_bearing(lat, lon, a_lat, a_lon)
    if d_km < 1e-3:
        return 90.0, az
    drop_m = (d_km * 1000.0) ** 2 / (2 * _EARTH_KM * 1000.0)
    return math.degrees(math.atan2(alt_m - drop_m, d_km * 1000.0)), az


def _separation(alt1: float, az1: float, alt2: float, az2: float) -> float:
    a1, a2 = math.radians(alt1), math.radians(alt2)
    c = math.sin(a1) * math.sin(a2) + math.cos(a1) * math.cos(a2) * math.cos(
        math.radians(az1 - az2)
    )
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def _num(d: dict[str, Any], key: str) -> float | None:
    v = d.get(key)
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v):
        return None
    return float(v)


def parse(data: object, lat: float, lon: float) -> list[AircraftPosition]:
    """The aircraft of an `aircraft.json` with a position no older than 60 s and within
    150 km, nearest first, at most 200 (SPEC §6.3 `aircraft`)."""
    if not isinstance(data, dict):
        raise ValueError("not an aircraft.json")
    raw: object = cast("dict[str, Any]", data).get("aircraft")
    if not isinstance(raw, list):
        raise ValueError("not an aircraft.json")
    found: list[tuple[float, AircraftPosition]] = []
    for item in cast("list[Any]", raw):
        if not isinstance(item, dict):
            continue
        a = cast("dict[str, Any]", item)
        hexid = a.get("hex")
        a_lat, a_lon = _num(a, "lat"), _num(a, "lon")
        # "~" marks a non-ICAO address (TIS-B); no position, nothing to draw or match.
        if not isinstance(hexid, str) or hexid.startswith("~") or a_lat is None or a_lon is None:
            continue
        seen = _num(a, "seen_pos")
        seen = _num(a, "seen") if seen is None else seen
        if seen is None or not 0 <= seen <= MAX_AGE_S:
            continue
        d_km, _ = _distance_bearing(lat, lon, a_lat, a_lon)
        if d_km > RANGE_KM:
            continue
        alt_ft = _num(a, "alt_baro")  # "ground" is a string: on the ground, no altitude
        alt_ft = _num(a, "alt_geom") if alt_ft is None else alt_ft
        track = _num(a, "track")
        flight = a.get("flight")
        try:
            pos = AircraftPosition(
                hex=hexid.strip().lower(),
                flight=flight.strip()[:8] or None if isinstance(flight, str) else None,
                lat=a_lat,
                lon=a_lon,
                alt_m=round(alt_ft * _FT) if alt_ft is not None else None,
                track_deg=track % 360.0 if track is not None else None,
                speed_kn=gs if (gs := _num(a, "gs")) is not None and gs >= 0 else None,
                seen_s=round(seen, 1),
            )
        except ValueError:
            continue
        found.append((d_km, pos))
    found.sort(key=lambda x: x[0])
    return [p for _, p in found[:MAX_AIRCRAFT]]


@dataclass(frozen=True)
class _Fix:
    at: datetime
    lat: float
    lon: float
    alt_m: float
    flight: str | None


class Tracks:
    """The positions of the last 10 minutes per ICAO address."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._fixes: dict[str, list[_Fix]] = {}
        self.newest: datetime | None = None

    def add(self, at: datetime, aircraft: list[AircraftPosition]) -> None:
        with self._lock:
            for a in aircraft:
                if a.alt_m is None:
                    continue
                when = at - timedelta(seconds=a.seen_s)
                fixes = self._fixes.setdefault(a.hex, [])
                if fixes and fixes[-1].at >= when:
                    continue  # the same position again
                fixes.append(_Fix(when, a.lat, a.lon, a.alt_m, a.flight))
            self.newest = at
            cut = at - KEEP
            for hexid in list(self._fixes):
                kept = [f for f in self._fixes[hexid] if f.at >= cut]
                if kept:
                    self._fixes[hexid] = kept
                else:
                    del self._fixes[hexid]

    def paths(self, start: datetime, end: datetime) -> dict[str, tuple[str | None, list[_Fix]]]:
        """Per aircraft its positions every second between `start` and `end`, linear between
        fixes (never across a gap of more than 30 s), with its last call sign."""
        out: dict[str, tuple[str | None, list[_Fix]]] = {}
        with self._lock:
            items = [(h, list(f)) for h, f in self._fixes.items()]
        for hexid, fixes in items:
            pts: list[_Fix] = []
            for a, b in itertools.pairwise(fixes):
                if b.at < start or a.at > end or b.at - a.at > MAX_GAP:
                    continue
                t = max(a.at, start)
                span = (b.at - a.at).total_seconds()
                while t <= min(b.at, end):
                    k = (t - a.at).total_seconds() / span if span > 0 else 0.0
                    pts.append(_Fix(t, a.lat + k * (b.lat - a.lat), a.lon + k * (b.lon - a.lon),
                                    a.alt_m + k * (b.alt_m - a.alt_m), b.flight))  # fmt: skip
                    t += STEP
            if pts:
                out[hexid] = (fixes[-1].flight, pts)
        return out


def match(event: Event, tracks: Tracks, lat: float, lon: float) -> str | None:
    """The call sign (else the ICAO address) of the aircraft a meteor is, or None (SPEC
    §7.8): both track ends within 2° of the aircraft's apparent path over the event's
    time ± 30 s, the aircraft above 5° for part of it; the nearest wins."""
    d = event.data
    ends = (d.get("start_altitude_deg"), d.get("start_azimuth_deg"),
            d.get("end_altitude_deg"), d.get("end_azimuth_deg"))  # fmt: skip
    if event.kind is not EventKind.METEOR or not all(
        isinstance(v, int | float) and not isinstance(v, bool) for v in ends
    ):
        return None
    alt1, az1, alt2, az2 = (float(cast("float", v)) for v in ends)
    best: tuple[float, str] | None = None
    for hexid, (flight, pts) in tracks.paths(event.start - SLACK, event.end + SLACK).items():
        seen = [apparent(lat, lon, p.lat, p.lon, p.alt_m) for p in pts]
        if not any(alt > MIN_ALT_DEG for alt, _ in seen):
            continue
        d1 = min(_separation(alt1, az1, alt, az) for alt, az in seen)
        d2 = min(_separation(alt2, az2, alt, az) for alt, az in seen)
        worst = max(d1, d2)
        if worst <= TOLERANCE_DEG and (best is None or worst < best[0]):
            best = (worst, f"{flight} {hexid}" if flight else hexid)
    return best[1] if best is not None else None


class AdsbReader:
    """Reads the receiver, relays to the hub and checks meteors once positions after
    their end are in (SPEC §7.8)."""

    def __init__(
        self,
        url: str,
        location: Callable[[], tuple[float, float] | None],
        relay: Callable[[Aircraft], None],
        on_match: Callable[[Event, str], None],
        client: Callable[[], httpx.Client] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._url = url
        self._client = client or (lambda: httpx.Client(timeout=5.0))
        self._location = location
        self._relay = relay
        self._on_match = on_match
        self._now = now
        self.tracks = Tracks()
        self._lock = threading.Lock()
        self._status = AdsbStatus()
        self._pending: list[Event] = []
        self._last_relay: datetime | None = None
        self._stop = threading.Event()

    @property
    def status(self) -> AdsbStatus:
        with self._lock:
            return self._status

    def check_later(self, event: Event) -> None:
        """A new meteor: checked once the receiver's positions reach past its end."""
        if event.kind is EventKind.METEOR and not event.imported:
            with self._lock:
                self._pending.append(event)

    def poll(self) -> None:
        now = self._now()
        loc = self._location()
        if loc is None:
            return
        try:
            with self._client() as http:
                r = http.get(self._url)
            r.raise_for_status()
            aircraft = parse(r.json(), *loc)
        except httpx.HTTPError as exc:
            log.debug("ADS-B receiver not reachable: %s", exc)
            with self._lock:
                self._status = self._status.model_copy(update={"error": "unreachable"})
            return
        except ValueError:
            with self._lock:
                self._status = self._status.model_copy(update={"error": "bad_data"})
            return
        self.tracks.add(now, aircraft)
        with self._lock:
            self._status = AdsbStatus(at=now, aircraft=len(aircraft))
            due = [e for e in self._pending if e.end + SLACK <= now]
            self._pending = [e for e in self._pending if e.end + SLACK > now]
            relay = self._last_relay is None or now - self._last_relay >= timedelta(seconds=RELAY_S)
            if relay:
                self._last_relay = now
        if relay:
            self._relay(Aircraft(at=now, aircraft=aircraft))
        for event in due:
            if event.start - SLACK < now - KEEP:
                continue  # positions no longer kept
            hit = match(event, self.tracks, *loc)
            if hit is not None:
                log.info("meteor %s is aircraft %s", event.id, hit)
                self._on_match(event, hit)

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll()
            except Exception:  # the receiver must never stop the agent
                log.exception("ADS-B poll failed")
            self._stop.wait(POLL_S)

    def start(self) -> None:
        threading.Thread(target=self.run, name="adsb", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
