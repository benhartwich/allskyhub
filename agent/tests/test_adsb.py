"""The owner's ADS-B receiver (SPEC §7.8, roadmap H3)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from allskyhub_agent.adsb import AdsbReader, Tracks, apparent, match, parse
from allskyhub_protocol import Aircraft, AircraftPosition, Event, EventKind

LAT, LON = 48.0, 11.0
T0 = datetime(2026, 10, 13, 21, 0, tzinfo=UTC)


def _plane(hexid: str, lat: float, lon: float, **kw: object) -> dict[str, object]:
    return {"hex": hexid, "lat": lat, "lon": lon, "alt_baro": 35000, "seen_pos": 0.5} | kw


def test_parse_keeps_near_fresh_positions_nearest_first() -> None:
    data = {
        "now": 0,
        "aircraft": [
            _plane("3c6444", 48.5, 11.0, flight="DLH4AB  ", gs=450.2, track=90.0),
            _plane("4b1805", 48.1, 11.0),
            _plane("~1234ab", 48.1, 11.0),  # TIS-B, no ICAO address
            _plane("3c0001", 50.0, 11.0),  # 222 km away
            _plane("3c0002", 48.1, 11.1, seen_pos=75),  # position too old
            {"hex": "3c0003", "seen": 1},  # no position
            _plane("3c0004", 48.2, 11.0, alt_baro="ground"),
        ],
    }
    got = parse(data, LAT, LON)
    assert [a.hex for a in got] == ["4b1805", "3c0004", "3c6444"]
    far = got[2]
    assert far.flight == "DLH4AB"
    assert far.alt_m == round(35000 * 0.3048)
    assert far.speed_kn == pytest.approx(450.2)
    assert got[1].alt_m is None  # on the ground
    with pytest.raises(ValueError, match=r"aircraft\.json"):
        parse({"planes": []}, LAT, LON)


def test_apparent_height() -> None:
    alt, az = apparent(LAT, LON, LAT + 0.09, LON, 10000.0)  # 10 km north, 10 km up
    assert az == pytest.approx(0.0, abs=0.5)
    assert alt == pytest.approx(45.0, abs=0.5)
    alt_far, _ = apparent(LAT, LON, LAT + 1.3, LON, 10000.0)  # 145 km: curvature counts
    assert 2.0 < alt_far < 3.5


def _flight(tracks: Tracks, start: datetime, seconds: int, step: int = 5) -> None:
    """DLH4AB flying east 30 km north of the camera at 10 km, 0.0025° lon per second."""
    for s in range(0, seconds + 1, step):
        tracks.add(
            start + timedelta(seconds=s),
            [AircraftPosition(hex="3c6444", flight="DLH4AB", lat=LAT + 0.27,
                              lon=LON - 0.2 + 0.0025 * s, alt_m=10000, seen_s=0)],
        )  # fmt: skip


def _meteor(
    start: datetime, end: datetime, a: tuple[float, float], b: tuple[float, float]
) -> Event:
    return Event(
        id="meteor-20261013T210100Z",
        night_id="20261013",
        kind=EventKind.METEOR,
        start=start,
        end=end,
        confidence=0.9,
        data={"start_altitude_deg": a[0], "start_azimuth_deg": a[1],
              "end_altitude_deg": b[0], "end_azimuth_deg": b[1]},
    )  # fmt: skip


def test_match_the_aircraft_on_the_track() -> None:
    tracks = Tracks()
    _flight(tracks, T0, 300)
    # Where the plane was 60 s and 90 s in: the "meteor" is its trail in a 30 s exposure.
    p1 = apparent(LAT, LON, LAT + 0.27, LON - 0.2 + 0.0025 * 60, 10000)
    p2 = apparent(LAT, LON, LAT + 0.27, LON - 0.2 + 0.0025 * 90, 10000)
    start, end = T0 + timedelta(seconds=60), T0 + timedelta(seconds=90)
    assert match(_meteor(start, end, p1, p2), tracks, LAT, LON) == "DLH4AB 3c6444"
    # A real meteor elsewhere in the sky at the same time is left alone.
    assert match(_meteor(start, end, (60.0, 180.0), (50.0, 200.0)), tracks, LAT, LON) is None
    # The same track ten minutes later: the plane is far away by then.
    later = timedelta(minutes=10)
    assert match(_meteor(start + later, end + later, p1, p2), tracks, LAT, LON) is None
    # Without the track ends (no orientation or clock) nothing is matched.
    bare = _meteor(start, end, p1, p2).model_copy(update={"data": {}})
    assert match(bare, tracks, LAT, LON) is None


def test_reader_relays_and_checks_after_the_end() -> None:
    now = [T0]
    pos = [LON - 0.2]
    relayed: list[Aircraft] = []
    found: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = {"aircraft": [_plane("3c6444", LAT + 0.27, pos[0], flight="DLH4AB",
                                    alt_baro=10000 / 0.3048, seen_pos=0)]}  # fmt: skip
        return httpx.Response(200, content=json.dumps(body))

    reader = AdsbReader(
        "http://receiver/data/aircraft.json",
        location=lambda: (LAT, LON),
        relay=relayed.append,
        on_match=lambda e, d: found.append((e.id, d)),
        client=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
        now=lambda: now[0],
    )
    p1 = apparent(LAT, LON, LAT + 0.27, LON - 0.2 + 0.0025 * 60, 10000)
    p2 = apparent(LAT, LON, LAT + 0.27, LON - 0.2 + 0.0025 * 90, 10000)
    for s in range(0, 121, 5):
        now[0] = T0 + timedelta(seconds=s)
        pos[0] = LON - 0.2 + 0.0025 * s
        if s == 95:  # saved after its exposure ended at 90 s
            start, end = T0 + timedelta(seconds=60), T0 + timedelta(seconds=90)
            reader.check_later(_meteor(start, end, p1, p2))
        reader.poll()
        if s < 120:
            assert found == []  # not before positions reach past end + 30 s
    assert found == [("meteor-20261013T210100Z", "DLH4AB 3c6444")]
    assert len(relayed) == 13  # every 10 s, not on every 5 s read
    assert relayed[0].aircraft[0].hex == "3c6444"
    assert reader.status.aircraft == 1
    assert reader.status.error is None


def test_reader_status_on_errors() -> None:
    answers = [httpx.Response(200, content=b"<html>"), httpx.Response(500)]

    def handler(request: httpx.Request) -> httpx.Response:
        return answers.pop(0)

    reader = AdsbReader(
        "http://receiver/aircraft.json",
        location=lambda: (LAT, LON),
        relay=lambda a: None,
        on_match=lambda e, d: None,
        client=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
        now=lambda: T0,
    )
    reader.poll()
    assert reader.status.error == "bad_data"
    reader.poll()
    assert reader.status.error == "unreachable"


def test_setting_adsb_url() -> None:
    from allskyhub_agent.profiles import get_profile
    from allskyhub_agent.settings import AgentSettings
    from allskyhub_agent.settings_schema import build_schema, check

    schema = build_schema(get_profile("zwo-asi678mc"))
    assert check(schema, {"adsb_url": "http://192.168.1.40/tar1090/data/aircraft.json"}) == []
    assert check(schema, {"adsb_url": ""}) == []
    assert check(schema, {"adsb_url": "192.168.1.40"}) == ["adsb_url"]
    s = AgentSettings().with_values({"adsb_url": " http://r/aircraft.json "})
    assert s.adsb_url == "http://r/aircraft.json"


def test_amend_keeps_the_newer_line(tmp_path: Path) -> None:
    from zoneinfo import ZoneInfo

    from allskyhub_agent.detect.events import EventStore
    from allskyhub_agent.store.images import ImageStore

    store = EventStore(ImageStore(tmp_path, ZoneInfo("Europe/Berlin")))
    e = _meteor(T0, T0 + timedelta(seconds=30), (40.0, 10.0), (42.0, 20.0))
    store._append(e)  # pyright: ignore[reportPrivateUsage]
    store.amend(e, {"aircraft": "DLH4AB"})
    (only,) = store.night_events("20261013")
    assert only.data["aircraft"] == "DLH4AB"
    assert only.data["start_altitude_deg"] == 40.0
