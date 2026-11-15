"""Flat field from twilight and night frames (SPEC §4.10)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from allskyhub_agent.adapters.camera import Image
from allskyhub_agent.process.flat import Curve, FlatConfig, FlatField, rho_grid

T0 = datetime(2026, 11, 15, 15, 40, tzinfo=UTC)
FRAC = 0.48
H, W = 120, 160
RHO = rho_grid(H, W, 0.5, 0.5, FRAC)
VIGNETTE = np.exp(-0.5 * RHO**2)  # the edge 1.65 times darker
INSIDE = RHO <= 1.0


def _twilight(i: int, level: float = 0.25) -> Image:
    rng = np.random.default_rng(i)
    xx = np.mgrid[0:H, 0:W][1] / W - 0.5
    sky = level * VIGNETTE * (1.0 + 0.2 * xx)  # brighter toward the sun in the east
    noisy = sky * 255 + rng.normal(0, 0.5, (H, W))
    gray = np.minimum(np.maximum(noisy, 0), 255) * INSIDE
    img = np.empty((H, W, 3), dtype=np.uint8)
    for c in range(3):
        img[:, :, c] = gray.astype(np.uint8)
    return img


def _flat(tmp_path: Path) -> FlatField:
    return FlatField(tmp_path / "flat.npz", srgb=False, circle_frac=FRAC)


def _feed_twilight(flat: FlatField, n: int = 25) -> None:
    for i in range(n):
        flat.observe_twilight(T0 + timedelta(minutes=i), -4.0, _twilight(i), 0.2, True, INSIDE)


def test_twilight_flat_without_the_sun_gradient(tmp_path: Path) -> None:
    flat = _flat(tmp_path)
    assert flat.status()["source"] is None
    _feed_twilight(flat)
    st = flat.status()
    assert st["source"] == "twilight"
    assert abs(float(st["max_gain"]) - 1.65) < 0.1  # type: ignore[arg-type]
    # A vignetted frame comes out even: the edge as bright as the centre.
    out = flat.apply(_twilight(99)).astype(np.float64)[:, :, 1]
    ring = (RHO > 0.8) & (RHO < 0.95)
    centre = RHO < 0.2
    xx = np.mgrid[0:H, 0:W][1] / W - 0.5
    even = out / (1.0 + 0.2 * xx)
    assert abs(float(even[ring].mean()) / float(even[centre].mean()) - 1.0) < 0.05
    # Kept across a restart.
    assert _flat(tmp_path).status()["source"] == "twilight"


def test_only_twilight_frames_count(tmp_path: Path) -> None:
    flat = _flat(tmp_path)
    for i in range(30):
        at = T0 + timedelta(minutes=i)
        flat.observe_twilight(at, 5.0, _twilight(i), 0.2, True, INSIDE)  # sun too high
        flat.observe_twilight(at, -4.0, _twilight(i), 0.5, True, INSIDE)  # too bright
        flat.observe_twilight(at, -4.0, _twilight(i), 0.2, False, INSIDE)  # gain raised
    assert flat.status()["source"] is None


def test_a_restless_twilight_is_refused(tmp_path: Path) -> None:
    flat = _flat(tmp_path)
    for i in range(25):  # the sky changes shape while it gets dark
        img = _twilight(i)
        if i >= 13:
            img[:, : W // 2] = img[:, : W // 2] // 2
        flat.observe_twilight(T0 + timedelta(minutes=i), -4.0, img, 0.2, True, INSIDE)
    st = flat.status()
    assert st["source"] is None
    assert "Zonen" in str(st["reason"])


def test_night_flat_after_two_agreeing_nights_and_not_for_measuring(tmp_path: Path) -> None:
    flat = _flat(tmp_path)
    median = (60 * VIGNETTE * INSIDE).astype(np.uint8)
    flat.observe_night("20261113", median, INSIDE)
    st = flat.status()
    assert st["source"] is None
    assert "zweite Nacht" in str(st["reason"])
    flat.observe_night("20261114", median, INSIDE)
    assert flat.status()["source"] == "night"
    frame = _twilight(1)
    shown = flat.apply(frame)
    assert int(shown[5, W // 2, 1]) >= int(frame[5, W // 2, 1])
    back = flat.undo_for_measuring(shown).astype(np.int64)
    assert int(np.abs(back - frame.astype(np.int64))[INSIDE].max()) <= 2
    # A twilight flat replaces it, and is used for measuring as well.
    _feed_twilight(flat)
    assert flat.status()["source"] == "twilight"
    assert flat.undo_for_measuring(shown) is shown


def test_no_correction_when_the_lens_hardly_darkens(tmp_path: Path) -> None:
    """libcamera corrects lens shading itself: a flat near 1 is not applied."""
    assert Curve((-0.01, 0.0, 0.0)).problem(FlatConfig()) is not None
    assert Curve((-1.0, 0.0, 0.0)).problem(FlatConfig()) is not None  # edge x2.7: too much
    assert Curve((-0.5, 0.0, 0.0)).problem(FlatConfig()) is None
    flat = FlatField(tmp_path / "flat.npz", srgb=True, circle_frac=FRAC)
    even = np.full((H, W, 3), 128, dtype=np.uint8)
    for i in range(25):
        flat.observe_twilight(T0 + timedelta(minutes=i), -4.0, even, 0.2, True, INSIDE)
    st = flat.status()
    assert st["source"] is None
    assert "keine Korrektur" in str(st["reason"])
    assert flat.apply(even) is even
