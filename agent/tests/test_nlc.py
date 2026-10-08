"""Noctilucent clouds (roadmap #8, SPEC §6.4) on a synthetic twilight frame."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import cv2
import numpy as np

from allskyhub_agent.calib.solve import Solution
from allskyhub_agent.core.sun import sun_position
from allskyhub_agent.detect.nlc import NlcConfig, NlcDetector, band_mask, score
from allskyhub_protocol import FrameInfo, Mode

H, W = 1080, 1920
LAT, LON = 48.136, 14.389
T0 = datetime(2026, 6, 21, 20, 45, tzinfo=UTC)  # deep twilight, sun low in the north-west
SOL = Solution(cx=960.0, cy=540.0, a1=1000.0, a3=-130.0, rot=18.2, flip=-1.0, tilt_east=0.0,
               tilt_north=0.0, stars=30, rms_px=1.0, rms_deg=0.1, width=W, height=H)  # fmt: skip
CFG = NlcConfig()


def twilight(seed: int, nlc: bool, bright_haze: bool = False) -> np.ndarray:
    r = np.random.default_rng(seed)
    base = 70.0 if not bright_haze else 170.0
    img = np.empty((H, W, 3), dtype=np.float32)
    img[:, :] = (base * 1.05, base, base * 0.95)  # a dark, slightly warm twilight sky
    img += r.normal(0, 1.5, (H, W, 1)).astype(np.float32)
    if nlc:
        sun_az = sun_position(T0, LAT, LON)[1]
        x, y = SOL.project(np.array([25.0]), np.array([sun_az]))
        cx, cy = int(x[0]), int(y[0])
        for k in range(6):  # electric-blue ripples
            yy = cy - 60 + 20 * k
            cv2.line(img, (cx - 120, yy), (cx + 120, yy + 10), (90, 130, 190), 7)
    return np.minimum(np.maximum(img, 0), 255).astype(np.uint8)


def frame(i: int, sun: float = -12.0) -> FrameInfo:
    t = T0 + timedelta(minutes=2 * i)
    return FrameInfo(captured_at=t, night_id="20260621", name=f"f{i}.jpg", mode=Mode.NIGHT,
                     exposure_us=5_000_000, gain=100, mean=0.2, sun_elevation=sun,
                     profile="sim")  # fmt: skip


def band() -> np.ndarray:
    return band_mask(SOL, sun_position(T0, LAT, LON)[1], H, W, CFG)


def test_band_is_toward_the_sun() -> None:
    m = band()
    ys, xs = np.nonzero(m)
    assert m.mean() > 0.02
    # Sun in the north-west; mirrored image with north at 18° from up: the band is up
    # and to the right of the centre.
    assert float(ys.mean()) < 540
    assert float(xs.mean()) > 960


def test_blue_ripples_score_and_haze_does_not() -> None:
    s = score(twilight(1, nlc=True), band(), CFG)
    assert s is not None
    assert s.blobs >= 1
    assert s.index >= CFG.min_index
    assert s.blue > 30
    quiet = score(twilight(1, nlc=False), band(), CFG)
    assert quiet is not None
    assert quiet.blobs == 0
    haze = score(twilight(1, nlc=True, bright_haze=True), band(), CFG)
    assert haze is not None
    assert haze.blobs == 0  # a bright band interior: low cloud, not NLC


def test_detector_needs_orientation_and_forms_an_episode() -> None:
    unsolved = NlcDetector(orientation=lambda: None, location=lambda: (LAT, LON))
    assert [
        u for i in range(4) for u in unsolved.feed(frame(i), twilight(i, True), Path("x"))
    ] == []
    det = NlcDetector(orientation=lambda: SOL, location=lambda: (LAT, LON))
    ups = [u for i in range(4) for u in det.feed(frame(i), twilight(i, True), Path(f"/{i}.jpg"))]
    assert len(ups) == 1
    assert ups[0].episode.start.captured_at == frame(0).captured_at
    assert ups[0].episode.best.direction_deg is not None
    closed = det.feed(frame(5, sun=-20), twilight(5, False), Path("/5.jpg"))
    assert [u.episode.ongoing for u in closed] == [False]
