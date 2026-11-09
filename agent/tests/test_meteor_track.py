"""A meteor's track as points and the local star fit for bright ones (SPEC §4.8 Local fit
for bright meteors, §6.4 `track`, fireball package B)."""

from __future__ import annotations

import itertools
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import cv2
import numpy as np
import pytest

from allskyhub_agent.detect.events import DetectionWorker, EventStore
from allskyhub_agent.detect.meteor import Streak, angle_between, centre_line, sky_unit
from allskyhub_agent.store.images import ImageStore
from allskyhub_protocol import Event, FrameInfo, Mode
from allskyhub_solve import localfit
from allskyhub_solve.solve import Solution, alt_az, sidereal_deg

# A made-up place and a winter evening with Orion and Taurus high in the south.
LAT, LON = 47.0, 1.0
AT = datetime(2026, 1, 15, 21, 0, tzinfo=UTC)
H, W = 1400, 1400
SOL = Solution(
    cx=702.0, cy=696.0, a1=640.0, a3=-60.0, rot=12.0, flip=-1.0, tilt_east=0.0,
    tilt_north=0.0, stars=60, rms_px=1.0, rms_deg=0.31, width=W, height=H,
)  # fmt: skip
A, B = (165.0, 38.0), (205.0, 52.0)  # (az, alt) of the meteor's ends in the true sky


def _streak(p1: tuple[float, float], p2: tuple[float, float], bend: float = 0.0) -> Streak:
    cx, cy = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
    length = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
    return Streak(cx=cx, cy=cy, length=length, elongation=20.0,
                  angle_deg=math.degrees(math.atan2(p2[1] - p1[1], p2[0] - p1[0])) % 180,
                  p1=p1, p2=p2, area=100, peak=0.95, bend=(bend, 0.0, 0.0))  # fmt: skip


def test_centre_line_runs_from_p1_to_p2_evenly() -> None:
    s = _streak((100.0, 100.0), (300.0, 200.0))
    pts = centre_line(s, 5)
    assert pts[0] == pytest.approx((100.0, 100.0))
    assert pts[-1] == pytest.approx((300.0, 200.0))
    steps = [math.dist(a, b) for a, b in itertools.pairwise(pts)]
    assert max(steps) - min(steps) < 1e-6
    # A bent streak follows its bend: the middle off the line through both ends.
    bent = centre_line(_streak((100.0, 100.0), (300.0, 100.0), bend=-0.001), 3)
    assert bent[0][1] == pytest.approx(bent[2][1])
    assert abs(bent[1][1] - bent[0][1]) == pytest.approx(10.0)


def _true_to_image(d_alt: float, d_az: float, turn: float) -> np.ndarray:
    az, alt = _mid()
    return localfit.correction(localfit.unit(alt, az), d_alt, d_az, turn)


def _mid() -> tuple[float, float]:
    a, b = localfit.unit(A[1], A[0]), localfit.unit(B[1], B[0])
    return localfit.to_az_alt(a + b)


def _px(rot: np.ndarray, az: float, alt: float) -> tuple[float, float]:
    v = rot @ localfit.unit(alt, az)
    paz, palt = localfit.to_az_alt(v)
    x, y = SOL.project(np.array([palt]), np.array([paz]))
    return float(x[0]), float(y[0])


def _render(
    rot: np.ndarray, max_stars: int | None = None, seed: int = 3, at: datetime = AT
) -> np.ndarray:
    """The frame of a camera whose real orientation is SOL turned by `rot`: the catalogue
    stars (V <= 4) as blobs by magnitude, sky glow, noise and the meteor."""
    r = np.random.default_rng(seed)
    img = r.normal(28.0, 2.0, (H, W)).astype(np.float32)
    ra, dec, mag = localfit._stars()  # pyright: ignore[reportPrivateUsage]
    alt, az = alt_az(ra, dec, sidereal_deg(at, LON), LAT)
    mags = [float(m) for m in mag.tolist()]
    drawn = 0
    for i in sorted(range(len(mags)), key=mags.__getitem__):
        if alt[i] < 5 or mag[i] > 4.0:
            continue
        if max_stars is not None and drawn >= max_stars:
            break
        x, y = _px(rot, float(az[i]), float(alt[i]))
        if not (5 <= x < W - 5 and 5 <= y < H - 5):
            continue
        amp = 220.0 * 10 ** (-0.25 * (mag[i] - 0.0))
        cv2.circle(img, (round(x), round(y)), 1, float(min(230.0, 30.0 + amp)), -1)
        drawn += 1
    img = cv2.GaussianBlur(img, (0, 0), 1.2)
    _draw_meteor(img, rot, 240.0, 3)
    return np.minimum(np.maximum(img, 0), 255).astype(np.uint8)


def _arc(n: int = 60) -> list[tuple[float, float]]:
    """(az, alt) along the great circle from A to B: what a meteor really draws."""
    a, b = localfit.unit(A[1], A[0]), localfit.unit(B[1], B[0])
    omega = math.acos(float(a @ b))
    out: list[tuple[float, float]] = []
    for i in range(n + 1):
        t = i / n
        v = (math.sin((1 - t) * omega) * a + math.sin(t * omega) * b) / math.sin(omega)
        out.append(localfit.to_az_alt(v))
    return out


def _draw_meteor(img: np.ndarray, rot: np.ndarray, value: float, width: int) -> None:
    pts = np.array([[round(c) for c in _px(rot, az, alt)] for az, alt in _arc()], np.int32)
    cv2.polylines(img, [pts], False, value, width)


def _track_px(rot: np.ndarray, n: int = 64) -> list[tuple[float, float]]:
    return [_px(rot, az, alt) for az, alt in _arc(n - 1)]


def _streak_px(rot: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mask = np.zeros((H, W), np.uint8)
    _draw_meteor(mask, rot, 1.0, 3)
    ys, xs = np.nonzero(mask)
    return xs.astype(np.int64), ys.astype(np.int64)


def _fit(img: np.ndarray, rot: np.ndarray, **kw: float) -> localfit.LocalFit | None:
    return localfit.fit(img, SOL, _track_px(rot), AT, LAT, LON, streak_px=_streak_px(rot), **kw)


def _off(fit: localfit.LocalFit, rot: np.ndarray, az: float, alt: float) -> float:
    x, y = _px(rot, az, alt)
    got = fit.position(x, y)
    return angle_between(sky_unit(*got), sky_unit(az, alt))


def test_the_local_fit_finds_the_small_turn() -> None:
    rot = _true_to_image(0.35, -0.45, 0.6)
    fit = _fit(_render(rot), rot)
    assert fit is not None
    assert fit.stars >= localfit.MIN_STARS
    assert fit.rms_deg <= localfit.MAX_RMS_DEG
    assert fit.angles_deg == pytest.approx((0.35, -0.45, 0.6), abs=0.1)
    # The ends with the fit are where the meteor really was; the orientation alone is off.
    for az, alt in (A, B):
        assert _off(fit, rot, az, alt) < 0.08
    plain = localfit.LocalFit(SOL, np.eye(3), 0, 0.0, (0.0, 0.0, 0.0))
    assert _off(plain, rot, *A) > 0.3


def test_too_few_stars_no_fit() -> None:
    rot = _true_to_image(0.2, 0.2, 0.2)
    assert _fit(_render(rot, max_stars=5), rot) is None


def test_more_than_one_degree_is_not_bent_into_place() -> None:
    rot = _true_to_image(2.5, 0.0, 0.0)
    fit = _fit(_render(rot), rot)
    if fit is not None:  # bounded: never more than 1° per angle
        assert max(abs(a) for a in fit.angles_deg) <= localfit.MAX_CORRECTION_DEG
        assert fit.rms_deg <= localfit.MAX_RMS_DEG


def test_the_fit_gives_up_after_its_budget() -> None:
    rot = _true_to_image(0.3, 0.3, 0.3)
    assert _fit(_render(rot), rot, budget_s=0.0) is None


def test_another_frame_size_no_fit() -> None:
    rot = _true_to_image(0.3, 0.3, 0.3)
    small = cv2.resize(_render(rot), (W // 2, H // 2))
    assert _fit(small, rot) is None


# --- in the event -------------------------------------------------------------------------


def _frame(i: int) -> FrameInfo:
    t = AT + timedelta(minutes=i)
    return FrameInfo(captured_at=t, night_id="20260115", name=f"image-{t:%Y%m%d%H%M%S}.jpg",
                     mode=Mode.NIGHT, exposure_us=20_000_000, gain=100, mean=0.1,
                     sun_elevation=-40.0, profile="sim")  # fmt: skip


def _meteor(tmp_path: Path, rot: np.ndarray, trusted: bool = True,
            located: bool = True) -> Event:  # fmt: skip
    store = ImageStore(tmp_path, ZoneInfo("UTC"))
    events = EventStore(store, orientation=lambda: SOL,
                        location=(lambda: (LAT, LON)) if located else None,
                        time_trusted=lambda: trusted)  # fmt: skip
    got: list[Event] = []
    worker = DetectionWorker(store, on_event=got.append, events=events)
    # The meteor is in the second frame: the sky at its mid-exposure (SPEC §4.8).
    f1 = _frame(1)
    plain = _render(rot, at=f1.captured_at + timedelta(microseconds=f1.exposure_us // 2))
    before = plain.copy()
    _draw_meteor(before, rot, 28.0, 7)
    frames = [before, plain, before]
    for i, gray in enumerate(frames):
        rgb = np.empty((H, W, 3), dtype=np.uint8)
        rgb[:, :, :] = gray[:, :, None]
        stored = store.save(rgb, _frame(i).captured_at)
        f = _frame(i).model_copy(update={"name": stored.name, "night_id": stored.night_id})
        worker.process_frame(f, rgb)
    meteors = [e for e in got if e.kind.value == "meteor"]
    assert len(meteors) == 1
    return meteors[0]


def _ends_of(ev: Event) -> tuple[tuple[float, float], tuple[float, float]]:
    d = ev.data
    return ((float(d["start_azimuth_deg"]), float(d["start_altitude_deg"])),  # type: ignore[arg-type]
            (float(d["end_azimuth_deg"]), float(d["end_altitude_deg"])))  # type: ignore[arg-type]  # fmt: skip


def test_a_bright_meteor_carries_its_fitted_track(tmp_path: Path) -> None:
    rot = _true_to_image(0.35, -0.45, 0.6)
    ev = _meteor(tmp_path, rot)
    track = ev.data["track"]
    assert isinstance(track, list)
    assert 2 <= len(track) <= 32
    start, end = _ends_of(ev)
    # From the end in start_* to the end in end_*, evenly spaced (SPEC §6.4).
    assert angle_between(sky_unit(*track[0]), sky_unit(*start)) < 0.3
    assert angle_between(sky_unit(*track[-1]), sky_unit(*end)) < 0.3
    steps = [angle_between(sky_unit(*a), sky_unit(*b)) for a, b in itertools.pairwise(track)]
    assert max(steps) - min(steps) < 0.3
    # The local fit was used: its RMS, not the orientation's, and every point lies on the
    # meteor's real great circle (what triangulation needs; the ends along it depend on
    # where the streak fades).
    rms = ev.data["track_rms_deg"]
    assert isinstance(rms, float)
    assert rms <= localfit.MAX_RMS_DEG
    assert max(_off_circle(p) for p in track) < 0.1
    plain = _meteor(tmp_path / "plain", rot, located=False)
    assert isinstance(plain.data["track"], list)
    assert max(_off_circle(p) for p in plain.data["track"]) > 0.3


def _off_circle(point: list[float]) -> float:
    """Degrees from the great circle through the true ends A and B."""
    n = np.cross(sky_unit(*A), sky_unit(*B))
    n = n / math.sqrt(float(n @ n))
    return abs(math.degrees(math.asin(float(sky_unit(*point) @ n))))


def test_without_a_place_the_orientation_alone(tmp_path: Path) -> None:
    rot = _true_to_image(0.35, -0.45, 0.6)
    ev = _meteor(tmp_path, rot, located=False)
    assert isinstance(ev.data["track"], list)
    assert ev.data["track_rms_deg"] == SOL.rms_deg


def test_no_track_while_the_clock_is_not_trusted(tmp_path: Path) -> None:
    ev = _meteor(tmp_path, _true_to_image(0.0, 0.0, 0.0), trusted=False)
    assert ev.data["track"] is None
    assert ev.data["track_rms_deg"] is None
    assert ev.data["start_azimuth_deg"] is None
