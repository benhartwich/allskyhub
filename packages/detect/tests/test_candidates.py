"""The meteors of one frame and its neighbours (allskyhub_detect.candidates), on synthetic
night frames: the checks the camera applies to such a triplet."""

from __future__ import annotations

import math

import cv2
import numpy as np
import pytest

from allskyhub_detect import Candidate, MeteorConfig, candidates, track_px

H, W = 360, 640
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


def test_a_meteor_in_one_frame_is_a_candidate() -> None:
    found = candidates(sky(1), with_streak(sky(2), 300, 150, 140, 30), sky(3))
    assert len(found) == 1
    c = found[0]
    assert isinstance(c, Candidate)
    assert c.length_px > 100
    assert abs(c.angle_deg - 30) < 3
    assert 0.7 < c.peak <= 1.0  # (230 - 30) / 255, the brightest new light
    assert c.new_light >= MeteorConfig().min_new_light
    assert 2 <= len(c.track_px) <= 32
    assert c.track_px[0] == pytest.approx(c.streak.p1)  # from p1 to p2, as the camera's
    assert c.track_px[-1] == pytest.approx(c.streak.p2)
    assert len(track_px(c.streak, 5)) == 5


def test_static_stars_and_noise_are_no_candidates() -> None:
    assert candidates(sky(1), sky(2), sky(3)) == []


def test_a_star_in_all_three_frames_is_no_candidate() -> None:
    """A bright streak that is there before, in and after the frame (a drifting star near
    the horizon, a bloom) has no new light."""
    before, frame, after = (with_streak(sky(k), 300, 150, 140, 30) for k in (1, 2, 3))
    assert candidates(before, frame, after) == []


def test_something_that_goes_on_is_no_candidate() -> None:
    """A satellite moves on in the next frame."""
    before, frame, after = (
        sky(1),
        with_streak(sky(2), 200, 150, 120, 10),
        with_streak(sky(3), 330, 173, 120, 10),
    )
    assert candidates(before, frame, after) == []


def test_a_dashed_aircraft_line_is_no_candidate() -> None:
    def plane(img: np.ndarray) -> np.ndarray:
        out = img.copy()
        cv2.line(out, (100, 200), (352, 200), 80, 2)  # its dim continuous trail ...
        for k in range(14):  # ... and its strobe, 8 px on, 10 px off
            cv2.line(out, (100 + 18 * k, 200), (108 + 18 * k, 200), 230, 2)
        return out

    assert candidates(sky(1), plane(sky(2)), sky(3)) == []
    solid = with_streak(sky(2), 226, 200, 252, 0)
    assert len(candidates(sky(1), solid, sky(3))) == 1


def test_the_mask_is_honoured() -> None:
    before, frame, after = sky(1), with_streak(sky(2), 300, 150, 140, 30), sky(3)
    mask = np.ones((H, W), dtype=np.bool_)
    mask[:, 200:420] = False  # the streak lies outside the sky
    assert candidates(before, frame, after, mask=mask) == []
    assert len(candidates(before, frame, after, mask=np.ones((H, W), dtype=np.bool_))) == 1


def test_a_cloudy_or_mismatched_triplet_gives_nothing() -> None:
    cloudy = with_streak(sky(2), 500, 300, 140, 30)
    cloudy[100:250, 100:400] = 120
    assert candidates(sky(1), cloudy, sky(3)) == []
    small = np.zeros((H // 2, W // 2), dtype=np.uint8)
    assert candidates(small, with_streak(sky(2), 300, 150, 140, 30), sky(3)) == []
