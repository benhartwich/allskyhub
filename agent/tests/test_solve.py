"""Plate solve and orientation (SPEC §4.8) on a synthetic sky."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import cv2
import numpy as np
import pytest

from allskyhub_agent.calib import orientation as orientation_mod
from allskyhub_agent.calib.orientation import OrientationConfig, Orienter
from allskyhub_agent.calib.solve import Solution, catalogue, project, solve, unproject
from allskyhub_protocol import FrameInfo, Mode, SkyMetrics

H, W = 1080, 1920
AT = datetime(2026, 10, 7, 21, 1, tzinfo=UTC)
LAT, LON = 48.136, 14.389
TRUE = [985.0, 532.0, 1005.0, -130.0, 18.2, 0.0, 0.0]  # like Benjamin's lens at half size
FLIP = -1.0


def render(at: datetime, p: list[float] = TRUE, flip: float = FLIP, seed: int = 1) -> np.ndarray:
    """A night frame: glowing sky disc, the bright catalogue stars where the lens puts
    them (size by magnitude) and fainter random stars."""
    r = np.random.default_rng(seed)
    img = np.zeros((H, W), dtype=np.float32)
    cv2.circle(img, (int(p[0]), int(p[1])), int(p[2] + p[3]), 40.0, -1)
    img += r.normal(0, 2.0, (H, W)).astype(np.float32)
    cat = catalogue(at, LAT, LON, min_alt=0.0)
    x, y = project(cat.alt, cat.az, p, flip)
    for xx, yy, m in zip(x.tolist(), y.tolist(), cat.mag.tolist(), strict=True):
        if 0 <= xx < W and 0 <= yy < H:
            cv2.circle(img, (round(xx), round(yy)), 3 if m < 1.5 else 2, 230.0 - 25 * m, -1)
    for xx, yy in zip(r.integers(0, W, 400).tolist(), r.integers(0, H, 400).tolist(), strict=True):
        if math.hypot(xx - p[0], yy - p[1]) < p[2] + p[3]:
            img[yy, xx] = 90.0  # faint stars: single pixels
    img = cv2.GaussianBlur(img, (0, 0), 1.0)
    return np.minimum(np.maximum(img, 0), 255).astype(np.uint8)


def test_project_unproject_roundtrip() -> None:
    alt = np.array([10.0, 45.0, 80.0])
    az = np.array([30.0, 200.0, 315.0])
    p = [*TRUE[:5], 2.0, -1.5]
    x, y = project(alt, az, p, FLIP)
    a2, z2 = unproject(x, y, p, FLIP)
    assert float(np.abs(a2 - alt).max()) < 1e-6
    assert float(np.abs(z2 - az).max()) < 1e-6


def test_solves_a_synthetic_night() -> None:
    sol = solve(render(AT), AT, LAT, LON, static=render(AT + timedelta(minutes=30), seed=2))
    assert sol is not None
    assert sol.flip == FLIP
    assert sol.rot == pytest.approx(18.2, abs=0.5)
    assert sol.cx == pytest.approx(985, abs=4)
    assert sol.cy == pytest.approx(532, abs=4)
    assert sol.stars >= 12
    assert sol.rms_deg < 0.6


def test_no_stars_no_solution() -> None:
    blank = np.full((H, W), 40, dtype=np.uint8)
    assert solve(blank, AT, LAT, LON) is None


def frame(i: int, sun: float = -30.0) -> FrameInfo:
    t = AT + timedelta(minutes=10 * i)
    return FrameInfo(captured_at=t, night_id="20261007", name=f"f{i}.jpg", mode=Mode.NIGHT,
                     exposure_us=60_000_000, gain=100, mean=0.1, sun_elevation=sun,
                     profile="sim")  # fmt: skip


def sky(cloud: float, stars: int = 500) -> SkyMetrics:
    return SkyMetrics(at=AT, night_id="20261007", cloud_cover=cloud, sqm_mag=20.0, stars=stars)


SOL = Solution(cx=1.0, cy=2.0, a1=1000.0, a3=-100.0, rot=18.2, flip=-1.0, tilt_east=0.0,
               tilt_north=0.0, stars=30, rms_deg=0.3, rms_px=3.0)  # fmt: skip


def test_orienter_waits_for_a_clear_pair_and_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str]] = []

    def fake_solve(gray: np.ndarray, at: datetime, lat: float, lon: float,
                   static: np.ndarray | None = None) -> Solution:  # fmt: skip
        calls.append((str(at), str(static is not None)))
        return SOL

    monkeypatch.setattr(orientation_mod, "solve", fake_solve)
    img = tmp_path / "f.jpg"
    cv2.imwrite(str(img), np.zeros((10, 10), np.uint8))
    o = Orienter(tmp_path / "cal" / "orientation.json", location=lambda: (LAT, LON))
    assert o.latest is None
    assert not o.consider(frame(0), img, sky(0.5))  # cloudy
    assert not o.consider(frame(0, sun=-10), img, sky(0.0))  # not dark enough
    assert not o.consider(frame(0), img, sky(0.0))  # clear, but no earlier clear frame yet
    assert not o.consider(frame(1), img, sky(0.0))  # only 10 min apart
    assert o.consider(frame(2), img, sky(0.0))  # 20 min: solve
    o.join(5)
    latest = o.latest
    assert latest is not None
    assert latest.north_deg == 18.2
    assert latest.mirrored
    assert latest.stars == 30
    # Mid-exposure time.
    assert calls == [(str(frame(2).captured_at + timedelta(seconds=30)), "True")]
    assert not o.consider(frame(3), img, sky(0.0))  # solved: not again for a week
    again = Orienter(tmp_path / "cal" / "orientation.json", location=lambda: (LAT, LON))
    assert again.latest == latest  # after a restart


def test_orienter_gives_up_after_tries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_solution(*_: object, **__: object) -> None:
        return None

    monkeypatch.setattr(orientation_mod, "solve", no_solution)
    img = tmp_path / "f.jpg"
    cv2.imwrite(str(img), np.zeros((10, 10), np.uint8))
    o = Orienter(tmp_path / "o.json", location=lambda: (LAT, LON),
                 cfg=OrientationConfig(retry_s=0.0, max_tries=2))  # fmt: skip
    started = 0
    for i in range(10):
        if o.consider(frame(i), img, sky(0.0)):
            started += 1
        o.join(5)
    assert started == 2
    assert o.latest is None
