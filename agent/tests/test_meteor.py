"""Meteor detection (roadmap #1) on synthetic night frames."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import cv2
import numpy as np
import pytest

from allskyhub_agent.calib.solve import Solution, alt_az, sidereal_deg
from allskyhub_agent.detect.events import DetectionWorker, EventStore
from allskyhub_agent.detect.meteor import (
    MeteorConfig,
    MeteorDetector,
    MeteorHit,
    active_shower,
    find_streaks,
    match_shower,
    radiant,
    radiant_distance,
)
from allskyhub_agent.store.images import ImageStore
from allskyhub_protocol import Event, EventKind, FrameInfo, Mode

H, W = 360, 640
T0 = datetime(2026, 10, 8, 21, 0, tzinfo=UTC)
rng = np.random.default_rng(7)
STARS = (rng.integers(0, H, 120), rng.integers(0, W, 120))


def sky(seed: int) -> np.ndarray:
    """A night frame: dark sky with read noise and fixed stars."""
    r = np.random.default_rng(seed)
    img = r.normal(30, 2.0, (H, W)).astype(np.float32)
    img[STARS] = 200
    return np.minimum(np.maximum(img, 0), 255).astype(np.uint8)


def with_streak(img: np.ndarray, x: int, y: int, length: int, angle: float, value: int = 230):
    out = img.copy()
    dx, dy = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    p1 = (int(x - dx * length / 2), int(y - dy * length / 2))
    p2 = (int(x + dx * length / 2), int(y + dy * length / 2))
    cv2.line(out, p1, p2, value, 2)
    return out


def frame(i: int, mode: Mode = Mode.NIGHT) -> FrameInfo:
    t = T0 + timedelta(minutes=i)
    return FrameInfo(
        captured_at=t,
        night_id="20261008",
        name=f"image-{t:%Y%m%d%H%M%S}.jpg",
        mode=mode,
        exposure_us=30_000_000,
        gain=100,
        mean=0.12,
        sun_elevation=-30,
        profile="sim",
    )


def run(frames: list[np.ndarray], cfg: MeteorConfig | None = None) -> list[MeteorHit]:
    det = MeteorDetector(cfg)
    hits: list[MeteorHit] = []
    for i, img in enumerate(frames):
        hits += det.feed(frame(i), img, Path(f"/x/{i}.jpg"))
    hits += det.flush()
    return hits


def test_find_streaks_length_and_elongation() -> None:
    diff = np.zeros((H, W), dtype=np.uint8)
    diff = with_streak(diff, 300, 150, 120, 30)
    diff[50:54, 50:54] = 255  # a blob, not a streak
    diff = with_streak(diff, 500, 300, 20, 0)  # too short (twinkle)
    streaks = find_streaks(diff, MeteorConfig())
    assert len(streaks) == 1
    s = streaks[0]
    assert s.length == pytest.approx(120, rel=0.15)
    assert s.angle_deg == pytest.approx(30, abs=3)
    assert s.peak > 0.8


def test_single_frame_meteor_is_detected() -> None:
    frames = [sky(1), with_streak(sky(2), 300, 150, 140, 30), sky(3)]
    hits = run(frames)
    assert len(hits) == 1
    assert hits[0].frame.name == frame(1).name
    assert hits[0].streak.length > 100


def test_satellite_over_two_frames_is_rejected() -> None:
    frames = [
        sky(1),
        with_streak(sky(2), 200, 150, 120, 10),
        with_streak(sky(3), 330, 173, 120, 10),  # continues along its path
        sky(4),
    ]
    assert run(frames) == []


def test_twinkling_frame_with_many_streaks_is_ignored() -> None:
    busy = sky(2)
    for k in range(10):
        busy = with_streak(busy, 60 + 70 * k, 60 + 30 * (k % 3) * 3, 80, 15 * k)
    assert run([sky(1), busy, sky(3)]) == []


def test_recurring_spot_is_rejected() -> None:
    s = (300, 150, 100, 60)
    frames = [sky(0)]
    for k in range(1, 9):
        frames.append(with_streak(sky(k), *s) if k % 2 else sky(k))
    hits = run(frames)
    # Each appearance and disappearance leaves a hot spot; from the third one on, a streak
    # at that place is a spot that keeps changing (a star, a bloom), not a meteor.
    assert [h.frame.name for h in hits] == [frame(1).name, frame(3).name]


def test_cloudy_frame_is_skipped() -> None:
    cloudy = sky(2)
    cloudy[100:250, 100:400] = 120  # a cloud lit up over a large part of the sky
    assert run([sky(1), with_streak(cloudy, 500, 300, 140, 30), sky(3)]) == []


def test_meteor_in_last_frame_cannot_be_confirmed() -> None:
    # Confirmation needs the disappearance in the next frame.
    assert run([sky(1), with_streak(sky(2), 300, 150, 140, 30)]) == []


def test_drifting_bright_star_is_rejected() -> None:
    frames = [sky(0)]
    for k in range(1, 6):
        img = sky(k)
        cv2.circle(img, (200 + 12 * k, 300), 9, 255, -1)  # moves 12 px a frame
        frames.append(img)
    assert run(frames) == []


def test_dashed_trail_counts_many_runs() -> None:
    gray = np.full((H, W), 30, dtype=np.uint8)
    for k in range(14):  # a strobing aircraft: 8 px on, 10 px off
        cv2.line(gray, (100 + 18 * k, 200), (108 + 18 * k, 200), 220, 2)
    from allskyhub_agent.detect.meteor import Streak, dash_runs

    line = Streak(cx=217, cy=200, length=250, elongation=50, angle_deg=0, p1=(92, 200),
                  p2=(342, 200), area=300, peak=0.8)  # fmt: skip
    assert dash_runs(gray, line) >= 10
    solid = np.full((H, W), 30, dtype=np.uint8)
    cv2.line(solid, (100, 200), (340, 200), 220, 2)
    assert dash_runs(solid, line) <= 2


def test_day_frames_reset_detection() -> None:
    det = MeteorDetector()
    assert det.feed(frame(0, Mode.DAY), sky(1), Path("/x")) == []
    assert det.feed(frame(1, Mode.DAY), with_streak(sky(2), 300, 150, 140, 30), Path("/x")) == []
    assert det.flush() == []


def test_shower_by_date() -> None:
    assert active_shower(date(2026, 8, 12)) == "Perseids"
    assert active_shower(date(2026, 12, 14)) == "Geminids"
    assert active_shower(date(2026, 1, 3)) == "Quadrantids"  # across the new year
    assert active_shower(date(2026, 3, 1)) is None


def test_event_store_and_worker(tmp_path: Path) -> None:
    store = ImageStore(tmp_path, ZoneInfo("Europe/Vienna"))
    got: list[Event] = []
    worker = DetectionWorker(store, on_event=got.append)
    for i, gray in enumerate([sky(1), with_streak(sky(2), 300, 150, 140, 30), sky(3)]):
        rgb = np.empty((H, W, 3), dtype=np.uint8)
        rgb[:, :, :] = gray[:, :, None]
        stored = store.save(rgb, frame(i).captured_at)
        f = frame(i).model_copy(update={"name": stored.name, "night_id": stored.night_id})
        worker.process_frame(f, rgb)
    assert len(got) == 1
    ev = got[0]
    assert ev.kind is EventKind.METEOR
    assert ev.id.startswith("meteor-")
    assert ev.has_image
    assert set(ev.data) == {"length_px", "peak", "frames", "direction_deg", "shower",
                            "shower_match", "azimuth_deg", "altitude_deg"}  # fmt: skip
    assert ev.data["azimuth_deg"] is None  # no orientation yet
    assert ev.data["shower"] == "Orionids"  # 8 October
    assert ev.data["shower_match"] == "date"
    events = EventStore(store)
    assert events.night_events(ev.night_id) == [ev]
    assert events.current_events() == [ev]
    full = events.image_path(ev.night_id, ev.id, thumbnail=False)
    thumb = events.image_path(ev.night_id, ev.id, thumbnail=True)
    assert full is not None
    assert full.read_bytes()[:2] == b"\xff\xd8"
    assert thumb is not None
    assert events.image_path(ev.night_id, "../x", thumbnail=False) is None


def test_event_ids_are_unique_within_a_second(tmp_path: Path) -> None:
    store = ImageStore(tmp_path, ZoneInfo("UTC"))
    events = EventStore(store)
    s = find_streaks(with_streak(np.zeros((H, W), np.uint8), 300, 150, 140, 30), MeteorConfig())[0]
    a = events.save_meteor(MeteorHit(frame(1), tmp_path / "missing.jpg", s))
    b = events.save_meteor(MeteorHit(frame(1), tmp_path / "missing.jpg", s))
    assert b.id == a.id + "-2"
    assert not a.has_image  # the frame could not be read


def _vec(az: float, alt: float) -> np.ndarray:
    a, e = math.radians(az), math.radians(alt)
    return np.array([math.cos(e) * math.sin(a), math.cos(e) * math.cos(a), math.sin(e)])


def _along(
    start: tuple[float, float], toward: tuple[float, float], deg: float
) -> tuple[float, float]:
    """(az, alt) `deg` along the great circle from `start` toward `toward`."""
    a, b = _vec(*start), _vec(*toward)
    t = b - float(a @ b) * a
    t = t / math.sqrt(float(t @ t))
    r = math.radians(deg)
    v = math.cos(r) * a + math.sin(r) * t
    return math.degrees(math.atan2(float(v[0]), float(v[1]))) % 360.0, math.degrees(
        math.asin(float(v[2]))
    )


def test_radiant_drifts_from_its_peak() -> None:
    assert radiant("Perseids", date(2026, 8, 12)) == (48.0, 58.0)
    ra, dec = radiant("Perseids", date(2026, 8, 2))
    assert ra == pytest.approx(34.5)
    assert dec == pytest.approx(56.8)
    assert radiant("Quadrantids", date(2025, 12, 30))[0] == pytest.approx(230.0 - 5 * 0.8)


def test_radiant_distance() -> None:
    rad = (0.0, 40.0)
    e1 = _along(rad, (90.0, 40.0), 30.0)
    e2 = _along(rad, (90.0, 40.0), 45.0)
    d = radiant_distance(e1, e2, rad)
    assert d is not None
    assert d < 0.01
    assert radiant_distance(e2, e1, rad) == d  # a trail has no sign
    assert radiant_distance(_along(rad, e1, -10.0), e2, rad) is None  # crosses it
    side = radiant_distance(e1, e2, _along(rad, (0.0, 90.0), 10.0))
    assert side is not None
    assert 5.0 < side < 10.0


def test_shower_by_radiant() -> None:
    utc = datetime(2026, 8, 12, 23, 0, tzinfo=UTC)
    lat, lon = 48.2, 16.4
    ra, dec = radiant("Perseids", utc.date())
    alt, az = alt_az(np.array([ra]), np.array([dec]), sidereal_deg(utc, lon), lat)
    rad = (float(az[0]), float(alt[0]))
    e1, e2 = _along(rad, (200.0, 60.0), 25.0), _along(rad, (200.0, 60.0), 35.0)
    assert match_shower(e1, e2, utc, lat, lon) == "Perseids"
    # The same trail turned by 30 degrees about its midpoint misses the radiant: sporadic.
    m = _along(rad, (200.0, 60.0), 30.0)
    off = _along(m, _along(rad, (110.0, 30.0), 30.0), 5.0)
    assert match_shower(m, off, utc, lat, lon) is None
    assert match_shower(e1, e2, datetime(2026, 3, 1, 23, tzinfo=UTC), lat, lon) is None


def _meteor_at(tmp_path: Path, ends: list[tuple[float, float]], sol: Solution, lat: float,
               lon: float) -> Event:  # fmt: skip
    """Run the worker on three frames with a meteor between two sky positions (az, alt)."""
    store = ImageStore(tmp_path, ZoneInfo("UTC"))
    events = EventStore(store, orientation=lambda: sol, location=lambda: (lat, lon))
    got: list[Event] = []
    worker = DetectionWorker(store, on_event=got.append, events=events)
    xs, ys = sol.project(np.array([e[1] for e in ends]), np.array([e[0] for e in ends]))
    p1 = (round(float(xs[0])), round(float(ys[0])))
    p2 = (round(float(xs[1])), round(float(ys[1])))
    meteor = sky(2)
    cv2.line(meteor, p1, p2, 230, 2)
    for i, gray in enumerate([sky(1), meteor, sky(3)]):
        rgb = np.empty((H, W, 3), dtype=np.uint8)
        rgb[:, :, :] = gray[:, :, None]
        stored = store.save(rgb, frame(i).captured_at)
        f = frame(i).model_copy(update={"name": stored.name, "night_id": stored.night_id})
        worker.process_frame(f, rgb)
    assert len(got) == 1
    return got[0]


def test_meteor_event_shower_by_radiant(tmp_path: Path) -> None:
    sol = Solution(
        cx=W / 2, cy=H / 2, a1=170.0, a3=0.0, rot=0.0, flip=1.0, tilt_east=0.0, tilt_north=0.0,
        stars=40, rms_px=0.5, rms_deg=0.2, width=W, height=H,
    )  # fmt: skip
    t = frame(1).captured_at + timedelta(seconds=15)
    lat = 48.0
    ra, dec = radiant("Orionids", t.date())
    lon = (ra - sidereal_deg(t, 0.0) + 540.0) % 360.0 - 180.0  # the radiant on the meridian
    alt, az = alt_az(np.array([ra]), np.array([dec]), sidereal_deg(t, lon), lat)
    rad = (float(az[0]), float(alt[0]))
    toward = (270.0, 30.0)
    ev = _meteor_at(tmp_path / "a", [_along(rad, toward, 20.0), _along(rad, toward, 50.0)],
                    sol, lat, lon)  # fmt: skip
    assert ev.data["shower"] == "Orionids"
    assert ev.data["shower_match"] == "radiant"
    m = _along(rad, toward, 35.0)
    # A trail of the same length across that direction, through its middle.
    across = [_along(m, (0.0, 90.0), 15.0), _along(m, (0.0, 90.0), -15.0)]
    ev = _meteor_at(tmp_path / "b", across, sol, lat, lon)
    assert ev.data["shower"] is None  # sporadic
    assert ev.data["shower_match"] == "radiant"
