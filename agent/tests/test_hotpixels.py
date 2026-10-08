"""Hot pixels without dark frames (SPEC §4.6)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from allskyhub_agent.process.hotpixels import HotPixelConfig, HotPixels

H, W = 240, 320
T0 = datetime(2026, 10, 8, 20, 0, tzinfo=UTC)
HOT = [(50, 60), (120, 200), (200, 33)]
LAMP = (slice(10, 20), slice(290, 300))  # a static light: a big spot, not a pixel defect


def night_frame(i: int) -> np.ndarray:
    r = np.random.default_rng(i)
    img = r.normal(30, 2.0, (H, W)).astype(np.float32)
    ys, xs = r.integers(0, H, 300), r.integers(0, W, 300)  # stars, elsewhere every frame
    img[ys, xs] = 220
    for y, x in HOT:
        img[y, x] = 200
    img[LAMP] = 180
    gray = np.minimum(np.maximum(img, 0), 255).astype(np.uint8)
    rgb = np.empty((H, W, 3), dtype=np.uint8)
    rgb[:, :, :] = gray[:, :, None]
    return rgb


def feed(
    hp: HotPixels, n: int, minutes: int = 15, sun: float = -30.0, night: str = "20261008"
) -> None:
    for i in range(n):
        hp.observe(T0 + timedelta(minutes=minutes * i), night, sun, night_frame(i))


def test_map_finds_hot_pixels_but_not_stars_or_lamps(tmp_path: Path) -> None:
    hp = HotPixels(tmp_path / "hot.npz")
    feed(hp, 13)  # 13 frames over 3 h
    found = set(zip(hp._ys.tolist(), hp._xs.tolist(), strict=True))  # pyright: ignore[reportPrivateUsage]
    assert found == set(HOT)


def test_not_enough_frames_or_span_builds_nothing(tmp_path: Path) -> None:
    hp = HotPixels(tmp_path / "hot.npz")
    feed(hp, 11)
    assert hp.count == 0
    hp = HotPixels(tmp_path / "hot.npz")
    feed(hp, 30, minutes=1)  # sampled once per 10 min: only 3 frames
    assert hp.count == 0
    hp = HotPixels(tmp_path / "hot.npz")
    feed(hp, 13, sun=-5)  # twilight frames are ignored
    assert hp.count == 0


def test_apply_replaces_hot_pixels_and_map_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "cal" / "hot.npz"
    hp = HotPixels(path)
    feed(hp, 13)
    img = night_frame(99)
    out = hp.apply(img)
    for y, x in HOT:
        assert img[y, x, 0] == 200
        assert abs(int(out[y, x, 0]) - 30) < 8
    assert out[LAMP].min() == 180  # untouched
    assert (out[100:110, 100:110] == img[100:110, 100:110]).all()
    again = HotPixels(path)
    assert again.count == len(HOT)
    assert (again.apply(img) == out).all()
    small = np.zeros((10, 10, 3), dtype=np.uint8)
    assert again.apply(small) is small  # another resolution: nothing to do


def test_too_many_spots_is_not_used(tmp_path: Path) -> None:
    hp = HotPixels(tmp_path / "hot.npz", HotPixelConfig(max_pixels=2))
    feed(hp, 13)
    assert hp.count == 0


def test_second_night_keeps_only_repeated_spots(tmp_path: Path) -> None:
    path = tmp_path / "hot.npz"
    hp = HotPixels(path)
    feed(hp, 13)
    assert hp.count == len(HOT)
    hp = HotPixels(path)  # a restart in between
    for i in range(13):
        img = night_frame(i)
        img[HOT[0]] = 30  # this one healed
        img[150, 150] = 200  # a new spot, seen only tonight
        hp.observe(T0 + timedelta(days=1, minutes=15 * i), "20261009", -30, img)
    found = set(zip(hp._ys.tolist(), hp._xs.tolist(), strict=True))  # pyright: ignore[reportPrivateUsage]
    assert found == set(HOT[1:])
