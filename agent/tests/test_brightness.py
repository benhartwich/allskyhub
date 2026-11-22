"""Sky brightness per sky cell (SPEC §6.3 `brightness_map`, roadmap L37)."""

from __future__ import annotations

import math
from datetime import UTC, datetime

import numpy as np
import numpy.typing as npt
import pytest

from allskyhub_agent.detect.brightness import (
    CELLS,
    BrightnessMeter,
    cell_centre,
    cell_medians,
    cloud_cell,
    solid_angle,
)
from allskyhub_agent.detect.cloudmap import cell_of
from allskyhub_solve.solve import Solution

W = H = 960
SOL = Solution(cx=480.0, cy=480.0, a1=450.0, a3=0.0, rot=20.0, flip=-1.0, tilt_east=0.0,
               tilt_north=0.0, stars=30, rms_px=1.0, rms_deg=0.1, width=W, height=H)  # fmt: skip
LAT, LON = 50.04, 10.39
NEW_MOON = datetime(2026, 10, 10, 22, 0, tzinfo=UTC)
FULL_MOON = datetime(2026, 10, 25, 23, 0, tzinfo=UTC)
NONE = np.zeros(0, dtype=np.intp)
# One meter for all tests: the geometry is worked out once per frame size and lens.
METER = BrightnessMeter(srgb=False)


def _sky() -> npt.NDArray[np.bool_]:
    ys, xs = np.mgrid[0:H, 0:W]
    return np.hypot(xs - SOL.cx, ys - SOL.cy) < SOL.a1


def _gray(values: npt.NDArray[np.float64]) -> npt.NDArray[np.uint8]:
    rgb = np.empty((H, W, 3), dtype=np.uint8)
    rgb[:, :, :] = np.round(np.minimum(values, 255.0)).astype(np.uint8)[:, :, None]
    return rgb


def test_every_cell_lies_in_one_cloud_map_cell() -> None:
    """SPEC §6.3: sector k covers k·22.5° .. (k+1)·22.5°, inside `cloud_map` cell
    8·band + ((k + 1) // 2) mod 8; both edges of the sector too."""
    for i in range(CELLS):
        alt, az = cell_centre(i)
        for a in (az - 11.2, az, az + 11.2):
            assert cell_of(alt, a % 360.0) == cloud_cell(i), (i, a)
    assert cell_centre(0) == (27.5, 11.25)
    assert cell_centre(47) == (77.5, 348.75)


def test_solid_angle_of_an_equidistant_lens() -> None:
    """Ω_px = sinθ / (r · dr/dθ), 1 at the lens centre; equidistant: sinθ / θ."""
    r = np.array([0.0, 300.0, 600.0])
    omega = solid_angle(SOL, SOL.cx + r, np.full(3, SOL.cy))
    thetas = [float(x) / SOL.a1 * math.pi / 2 for x in r]
    want = [math.sin(t) / t if t > 0 else 1.0 for t in thetas]
    assert omega.tolist() == pytest.approx(want, rel=1e-4)


def test_cell_medians_need_200_pixels() -> None:
    cell = np.array([0] * 200 + [1] * 199, dtype=np.int16)
    values = np.arange(399, dtype=np.float32)
    keep = np.ones(399, dtype=np.bool_)
    out = cell_medians(cell, values, keep)
    assert out[0] == pytest.approx(99.5)
    assert out[1] is None


def test_a_uniform_flat_sky_reads_the_sqm_value_everywhere() -> None:
    """Twilight mode: a frame divided by the flat that is uniform reads `sqm_mag` in every
    cell, as L_cell = L_ref."""
    frame = _gray(np.full((H, W), 60.0))
    cells = METER.measure(
        SOL, frame, frame, "twilight", _sky(), NONE, NONE, 21.2, NEW_MOON, LAT, LON
    )
    assert cells is not None
    assert len(cells) == CELLS
    assert cells == [21.2] * CELLS


def test_a_light_dome_is_brighter_by_its_ratio() -> None:
    """Four times the linear brightness in one cell is 1.5 mag brighter than the rest."""
    values = np.full((H, W), 40.0)
    geo_cell = _cells()
    dome = geo_cell == 4
    values[dome] = 160.0
    frame = _gray(values)
    cells = METER.measure(SOL, frame, frame, "twilight", _sky(), NONE, NONE, 21.0, NEW_MOON,
                          LAT, LON)  # fmt: skip
    assert cells is not None
    c4, c5 = cells[4], cells[5]
    assert c4 is not None
    assert c5 is not None
    assert c5 - c4 == pytest.approx(2.5 * math.log10(4.0), abs=0.02)


def test_geometry_mode_divides_by_the_solid_angle() -> None:
    """Without a twilight flat a uniform sky through an ideal lens gives each pixel a
    signal ∝ Ω_px: divided by it, all cells read the same."""
    ys, xs = np.mgrid[0:H, 0:W]
    omega = solid_angle(SOL, xs.ravel() + 0.5, ys.ravel() + 0.5).reshape(H, W)
    frame = _gray(200.0 * omega.astype(np.float64))
    cells = METER.measure(
        SOL, frame, frame, "geometry", _sky(), NONE, NONE, 21.0, NEW_MOON, LAT, LON
    )
    assert cells is not None
    values = [c for c in cells if c is not None]
    assert len(values) == CELLS
    assert max(values) - min(values) < 0.05


def test_stars_and_clipped_pixels_are_left_out() -> None:
    """Stars (3 px around each) and clipped pixels do not count: a cell full of them has
    fewer than 200 pixels left and is null."""
    values = np.full((H, W), 40.0)
    geo_cell = _cells()
    values[geo_cell == 7] = 255.0
    frame = _gray(values)
    ys, xs = np.nonzero(_cells() == 9)
    cells = METER.measure(
        SOL, frame, frame, "twilight", _sky(), xs.astype(np.intp), ys.astype(np.intp), 21.0,
        NEW_MOON, LAT, LON,
    )  # fmt: skip
    assert cells is not None
    assert cells[7] is None
    assert cells[9] is None
    # The clipped cell still counts for L_ref (the SQM statistic), the clear ones agree.
    assert cells[8] is not None
    assert cells[8] == cells[10]


def test_no_cell_near_a_lit_moon() -> None:
    frame = _gray(np.full((H, W), 60.0))
    cells = METER.measure(
        SOL, frame, frame, "twilight", _sky(), NONE, NONE, 19.0, FULL_MOON, LAT, LON
    )
    assert cells is not None
    assert 0 < cells.count(None) < CELLS


def _cells() -> npt.NDArray[np.int16]:
    """The cell of every full size pixel, from the 960 px geometry."""
    from allskyhub_agent.detect.brightness import _geometry  # pyright: ignore[reportPrivateUsage]

    geo = _geometry(H, W, SOL)
    sw, sh = geo.size
    rows = np.arange(H) * sh // H
    cols = np.arange(W) * sw // W
    return geo.cell[rows[:, None], cols[None, :]]
