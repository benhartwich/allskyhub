"""Sky mask learned from the night's frames (SPEC §4.7)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import cv2
import numpy as np

from allskyhub_agent.process.skymask import SkyMask, SkyMaskConfig

H, W = 540, 960
T0 = datetime(2026, 10, 8, 20, 0, tzinfo=UTC)


def night_frame(i: int, cloud: bool = False) -> np.ndarray:
    """Black corners, a glowing sky circle with stars, a dark tree in the lower right."""
    r = np.random.default_rng(i)
    img = np.zeros((H, W), dtype=np.float32)
    circle = np.zeros((H, W), dtype=np.uint8)
    cv2.circle(circle, (W // 2, H // 2), 300, 1, -1)
    img[circle > 0] = 45
    img[350:, 650:] = 8  # the tree
    img[100:140, 380:430] = 20  # a dark sky patch: still sky (a hole to fill)
    img += r.normal(0, 2.0, (H, W)).astype(np.float32)
    ys, xs = r.integers(0, H, 400), r.integers(0, W, 400)
    img[ys, xs] = 200  # stars, elsewhere every frame
    if cloud:
        img[50:200, 300:700] = 120
    gray = np.minimum(np.maximum(img, 0), 255).astype(np.uint8)
    rgb = np.empty((H, W, 3), dtype=np.uint8)
    rgb[:, :, :] = gray[:, :, None]
    return rgb


def feed(m: SkyMask, n: int, minutes: int = 15, sun: float = -30.0) -> None:
    for i in range(n):
        m.observe(
            T0 + timedelta(minutes=minutes * i), "20261008", sun, night_frame(i, cloud=i == 3)
        )


def test_learns_sky_without_tree_and_corners(tmp_path: Path) -> None:
    m = SkyMask(tmp_path / "cal" / "skymask.png")
    assert m.get(H, W) is None
    feed(m, 13)
    mask = m.get(H, W)
    assert mask is not None
    assert mask[H // 2, W // 2]
    assert mask[120, 400]  # the dark patch is filled
    assert not mask[500, 900]  # tree
    assert not mask[10, 10]  # corner outside the lens
    assert not mask[450, 700]  # tree inside the lens circle
    big = m.get(2 * H, 2 * W)
    assert big is not None
    assert big.shape == (2 * H, 2 * W)
    again = SkyMask(tmp_path / "cal" / "skymask.png")  # after a restart
    loaded = again.get(H, W)
    assert loaded is not None
    assert (loaded == mask).all()


def test_needs_a_dark_night(tmp_path: Path) -> None:
    m = SkyMask(tmp_path / "skymask.png")
    feed(m, 11)
    assert m.get(H, W) is None
    feed(m, 13, sun=-5)
    assert m.get(H, W) is None


def test_implausible_mask_is_not_used(tmp_path: Path) -> None:
    m = SkyMask(tmp_path / "skymask.png", SkyMaskConfig(max_sky=0.1))
    feed(m, 13)
    assert m.get(H, W) is None
    assert not (tmp_path / "skymask.png").exists()
