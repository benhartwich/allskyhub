"""Moon HDR (SPEC §4.13): the moon's patch, when to take short exposures, the fusion."""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

from allskyhub_agent.adapters.camera import Image
from allskyhub_agent.process import moonhdr

H, W = 400, 600


def _sky(t_s: float) -> Image:
    """A dark sky with a full moon and halo at (400, 200) and a lamp at (100, 300),
    linear in the exposure time `t_s`."""
    yy, xx = np.mgrid[0:H, 0:W]
    r = np.hypot(xx - 400.0, yy - 200.0)
    moon = 3e5 * np.exp(-((r / 6.0) ** 2)) + 400.0 * np.exp(-r / 25.0)
    lamp = 3000.0 * np.exp(-((np.hypot(xx - 100.0, yy - 300.0) / 3.0) ** 2))
    value: npt.NDArray[np.float64] = (0.6 + moon + lamp) * t_s
    rng = np.random.default_rng(1)
    noisy: npt.NDArray[np.float64] = value + rng.normal(0.0, 0.5, value.shape)
    mono = np.minimum(np.maximum(noisy, 0.0), 255.0).astype(np.uint8)
    out: Image = np.empty((H, W, 3), dtype=np.uint8)
    out[:, :, :] = mono[:, :, None]
    return out


LONG = _sky(20.0)
SHORTS = [_sky(us / 1e6) for us in reversed(moonhdr.SHORT_US)]  # longest first


def test_the_patch_is_the_moons() -> None:
    moon = moonhdr.moon_patch(LONG, (400.0, 200.0))
    lamp = moonhdr.moon_patch(LONG, (100.0, 300.0))
    assert moon is not None
    assert lamp is not None
    assert int(moon.sum()) > int(lamp.sum())
    assert not (moon & lamp).any()
    largest = moonhdr.moon_patch(LONG, None)  # without an orientation: the largest
    assert largest is not None
    assert (largest == moon).all()
    assert moonhdr.moon_patch(LONG, (5.0, 5.0)) is None  # dark there
    assert moonhdr.moon_patch(LONG, (-3.0, 5.0)) is None  # outside the frame


def test_short_exposures_only_for_a_patch_of_some_size() -> None:
    sky = np.ones((H, W), dtype=np.bool_)
    assert moonhdr.wanted(LONG, sky, (400.0, 200.0))
    assert not moonhdr.wanted(LONG, sky, (5.0, 5.0))
    assert not moonhdr.wanted(_sky(0.0005), sky, None)  # nothing burnt out


def test_fusion_only_around_the_moon() -> None:
    fused = moonhdr.fuse(LONG, SHORTS, (400.0, 200.0))
    assert fused is not None
    patch = moonhdr.moon_patch(LONG, (400.0, 200.0))
    assert patch is not None
    assert fused.region_px == int(patch.sum())
    out = fused.image.astype(int)
    # Outside the region the long frame exactly, the lamp too.
    assert (out[:, :200] == LONG[:, :200]).all()
    # Inside, a disc brighter than its halo, and the halo no longer white.
    assert out[200, 400, 0] > out[200, 430, 0]
    assert out[200, 430, 0] < moonhdr.SATURATED
    # No ring: at the region's edge the fused frame meets the long one.
    edge = out[200, 520, 0] - int(LONG[200, 520, 0])
    assert abs(edge) <= 12
    # Without a predicted position: the brightest point of the 1 ms frame.
    blind = moonhdr.fuse(LONG, SHORTS, None)
    assert blind is not None
    assert blind.region_px == fused.region_px


def test_nothing_to_fuse_without_a_burnt_out_moon() -> None:
    assert moonhdr.fuse(_sky(0.0005), [_sky(0.0001)] * 4, (400.0, 200.0)) is None
