"""Sky condition for status (SPEC §6.3 `sky`) on synthetic frames."""

from __future__ import annotations

from datetime import UTC, datetime

import cv2
import numpy as np
import pytest

from allskyhub_agent.detect.sky import SkyMeter, sqm
from allskyhub_protocol import FrameInfo, Mode, SkyMetrics, Status

H, W = 480, 640
T = datetime(2026, 10, 8, 22, 0, tzinfo=UTC)


def frame(sun: float, exposure_us: int = 30_000_000, gain: float = 100.0) -> FrameInfo:
    return FrameInfo(
        captured_at=T,
        night_id="20261008",
        name="image-20261008220000.jpg",
        mode=Mode.NIGHT if sun < -6 else Mode.DAY,
        exposure_us=exposure_us,
        gain=gain,
        mean=0.1,
        sun_elevation=sun,
        profile="sim",
    )


def starry(n: int, seed: int = 1, cover_left: bool = False) -> np.ndarray:
    r = np.random.default_rng(seed)
    gray = r.normal(20, 1.5, (H, W)).astype(np.float32)
    gray = np.minimum(np.maximum(gray, 0), 255).astype(np.uint8)
    for x, y in zip(r.integers(10, W - 10, n), r.integers(10, H - 10, n), strict=True):
        cv2.circle(gray, (int(x), int(y)), 2, 200, -1)
    if cover_left:
        gray[:, : W // 2] = 45  # an overcast half: no stars
    rgb = np.empty((H, W, 3), dtype=np.uint8)
    rgb[:, :, :] = gray[:, :, None]
    return rgb


def measure(rgb: np.ndarray, f: FrameInfo) -> SkyMetrics:
    return SkyMeter().measure(f, rgb)


def test_clear_night() -> None:
    m = measure(starry(800), frame(-30))
    assert m.stars is not None
    assert m.stars > 250  # about 430 lie in the image circle, some overlap
    assert m.cloud_cover is not None
    assert m.cloud_cover < 0.2
    assert m.sqm_mag is not None
    assert m.at == T
    assert m.night_id == "20261008"


def test_half_overcast_night() -> None:
    m = measure(starry(1500, cover_left=True), frame(-30))
    assert m.cloud_cover == pytest.approx(0.5, abs=0.12)


def test_no_sqm_in_nautical_dusk_and_nothing_in_civil_dusk() -> None:
    m = measure(starry(1500), frame(-14))
    assert m.stars is not None
    assert m.sqm_mag is None
    m = measure(starry(1500), frame(-3))
    assert (m.cloud_cover, m.sqm_mag, m.stars) == (None, None, None)


def test_nothing_by_day() -> None:
    rgb = np.empty((H, W, 3), dtype=np.uint8)
    rgb[:, :] = (90, 140, 220)
    m = measure(rgb, frame(30))
    assert (m.cloud_cover, m.sqm_mag, m.stars) == (None, None, None)


def test_sqm_normalises_exposure_and_gain() -> None:
    a = sqm(20.0, 30_000_000, 100.0, 18.8)
    b = sqm(40.0, 60_000_000, 100.0, 18.8)  # twice as long, twice the signal: same sky
    c = sqm(20.0, 30_000_000, 160.0, 18.8)  # 6 dB more gain, same level: darker sky
    assert a == b
    assert a is not None
    assert c is not None
    assert c == pytest.approx(a + 2.5 * 0.3, abs=0.02)
    assert sqm(0.0, 30_000_000, 0.0, 18.8) is None


def test_meter_keeps_latest_and_status_carries_it() -> None:
    meter = SkyMeter()
    assert meter.latest is None
    rgb = starry(800)
    m = meter.measure(frame(-30), rgb)
    assert meter.latest == m
    s = Status(mode=Mode.NIGHT, exposure_us=1, gain=0, mean=0.1, uptime_s=1,
               time_trusted=True, sky=m)  # fmt: skip
    assert Status.model_validate_json(s.model_dump_json()).sky == m


def test_learned_mask_keeps_trees_out_of_cloud_cover() -> None:
    rgb = starry(800)
    rgb[240:, 320:] = 5  # a tree in the lower right: no stars, not cloud
    meter = SkyMeter()
    with_tree = meter.measure(frame(-30), rgb).cloud_cover
    assert with_tree is not None
    assert with_tree > 0.15
    sky = np.ones((H, W), dtype=np.bool_)
    sky[240:, 320:] = False
    meter.set_mask(sky)
    without = meter.measure(frame(-30), rgb).cloud_cover
    clear = SkyMeter().measure(frame(-30), starry(800)).cloud_cover
    assert without is not None
    assert clear is not None
    assert without < with_tree - 0.15
    assert abs(without - clear) < 0.1  # as if the tree were not there
