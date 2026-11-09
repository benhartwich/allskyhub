"""Plate solving (SPEC §4.8) on a synthetic night, and the acceptance rule."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import cv2
import numpy as np
import pytest

from allskyhub_solve import MAX_RMS_DEG, MIN_STARS, Solution, accept, solve, solve_verdict
from allskyhub_solve.solve import catalogue, project, unproject

H, W = 1080, 1920
AT = datetime(2026, 10, 7, 21, 1, tzinfo=UTC)
LAT, LON = 47.5, 2.3  # an invented place
TRUE = [985.0, 532.0, 1005.0, -130.0, 18.2, 0.0, 0.0]
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
    sol, verdict = solve_verdict(
        render(AT), AT, LAT, LON, static=render(AT + timedelta(minutes=30), seed=2)
    )
    assert sol is not None
    assert verdict.ok
    assert verdict.reason == ""
    assert sol.flip == FLIP
    assert sol.rot == pytest.approx(18.2, abs=0.5)
    assert sol.cx == pytest.approx(985, abs=4)
    assert sol.cy == pytest.approx(532, abs=4)
    assert sol.stars >= MIN_STARS
    assert sol.rms_deg < MAX_RMS_DEG
    assert accept(sol.stars, sol.rms_deg)


def test_no_stars_no_solution() -> None:
    blank = np.full((H, W), 40, dtype=np.uint8)
    assert solve(blank, AT, LAT, LON) is None
    sol, verdict = solve_verdict(blank, AT, LAT, LON)
    assert sol is None
    assert not verdict
    assert verdict.reason == "few stars"


def test_acceptance_rule() -> None:
    """SPEC §4.8: ≥ 12 stars, ≤ 0.6° RMS, no rival with 70 % of the stars."""
    assert accept(12, 0.6)
    assert accept(30, 0.3, rival_stars=20)
    assert accept(11, 0.3).reason == "few stars"
    assert accept(30, 0.61).reason == "large error"
    assert accept(30, math.nan).reason == "large error"
    assert accept(30, 0.3, rival_stars=21).reason == "close rival"


def test_position_of_a_pixel() -> None:
    sol = Solution(cx=TRUE[0], cy=TRUE[1], a1=TRUE[2], a3=TRUE[3], rot=TRUE[4], flip=FLIP,
                   tilt_east=0.0, tilt_north=0.0, stars=30, rms_px=1.0, rms_deg=0.1,
                   width=W, height=H)  # fmt: skip
    x, y = project(np.array([42.0]), np.array([132.0]), TRUE, FLIP)
    pos = sol.position(float(x[0]), float(y[0]), W, H)
    assert pos == (132.0, 42.0)
    assert sol.position(float(x[0]), float(y[0]), 2 * W, 2 * H) is None  # another frame size
    az, alt = sol.position(TRUE[0], TRUE[1] - 300, W, H) or (0.0, 0.0)
    assert alt > 30
    assert az == pytest.approx(TRUE[4], abs=1)  # up in the image: (north_deg - 0) mirrored
