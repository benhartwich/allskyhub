"""Cloud cover per sky cell (SPEC §6.3 `cloud_map`, roadmap H4)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import numpy as np
import numpy.typing as npt
import pytest
from pydantic import ValidationError

from allskyhub_agent.calib.solve import Solution, alt_az, sidereal_deg
from allskyhub_agent.detect import cloudmap
from allskyhub_agent.detect.cloudmap import CELLS, cell_centre, cell_of
from allskyhub_protocol import SkyMetrics

W = H = 2000
SOL = Solution(cx=1000.0, cy=1000.0, a1=900.0, a3=0.0, rot=20.0, flip=-1.0, tilt_east=0.0,
               tilt_north=0.0, stars=30, rms_px=1.0, rms_deg=0.1, width=W, height=H)  # fmt: skip
LAT, LON = 48.2, 16.4
F = npt.NDArray[np.float64]
NEW_MOON = datetime(2026, 10, 10, 22, 0, tzinfo=UTC)
FULL_MOON = datetime(2026, 10, 25, 23, 0, tzinfo=UTC)


def test_cells() -> None:
    """SPEC §6.3: index = 8·band + sector, sector 0 centred on north, limits go up."""
    assert cell_of(10.0, 0.0) is None
    assert cell_of(15.0, 0.0) == 0
    assert cell_of(20.0, 359.0) == 0
    assert cell_of(20.0, 22.5) == 1
    assert cell_of(39.9, 90.0) == 2
    assert cell_of(40.0, 180.0) == 12
    assert cell_of(65.0, 270.0) == 22
    assert cell_of(90.0, 0.0) == 16
    assert cell_centre(13) == (52.5, 225.0)


def _stars_at(at: datetime) -> tuple[F, F, F, F]:
    ra, dec = cloudmap._stars()  # pyright: ignore[reportPrivateUsage]
    alt, az = alt_az(ra, dec, sidereal_deg(at, LON), LAT)
    up = alt >= 15.0
    x, y = SOL.project(alt[up], az[up])
    return alt[up], az[up], x, y


def _map(
    at: datetime,
    keep: Callable[[F, F], npt.NDArray[np.bool_]] | None = None,
    sky: npt.NDArray[np.bool_] | None = None,
) -> list[float | None]:
    alt, az, x, y = _stars_at(at)
    sel = np.ones(alt.size, dtype=np.bool_) if keep is None else keep(alt, az)
    # The detections are a pixel off, as a real centroid would be.
    xs = (np.round(x[sel]) + 1).astype(np.intp)
    ys = np.round(y[sel]).astype(np.intp)
    mask = np.ones((H, W), dtype=np.bool_) if sky is None else sky
    return cloudmap.cloud_map(SOL, (H, W), mask, xs, ys, at, 1_000_000, LAT, LON)


def test_clear_sky_reads_zero() -> None:
    m = _map(NEW_MOON)
    assert len(m) == CELLS
    judged = [v for v in m if v is not None]
    assert len(judged) >= 16
    assert all(v == 0.0 for v in judged)
    SkyMetrics.model_validate({"at": NEW_MOON, "night_id": "20261010", "cloud_map": m})


def _not_east(alt: F, az: F) -> npt.NDArray[np.bool_]:
    return ~((az >= 67.5) & (az < 112.5))


def test_cloud_in_the_east() -> None:
    m = _map(NEW_MOON, keep=_not_east)
    for band in range(3):
        east = m[8 * band + 2]
        assert east is None or east == 1.0
        north = m[8 * band]
        assert north is None or north == 0.0
    assert m[2] == 1.0


def test_obstructed_cells_are_null() -> None:
    sky = np.ones((H, W), dtype=np.bool_)
    alt, az, x, y = _stars_at(NEW_MOON)
    # Blank every star of the low western band in the mask: nothing expected there.
    for i in np.nonzero((alt < 40) & (az >= 247.5) & (az < 292.5))[0]:
        sky[int(y[i]) - 3 : int(y[i]) + 4, int(x[i]) - 3 : int(x[i]) + 4] = False
    assert _map(NEW_MOON)[6] is not None
    assert _map(NEW_MOON, sky=sky)[6] is None


def test_lit_moon_blanks_its_cells() -> None:
    m = _map(FULL_MOON)
    from allskyhub_sky.astro import moon_radec

    ra, dec = moon_radec(FULL_MOON)
    malt, maz = alt_az(np.array([ra]), np.array([dec]), sidereal_deg(FULL_MOON, LON), LAT)
    cell = cell_of(float(malt[0]), float(maz[0]))
    assert cell is not None
    assert m[cell] is None
    assert any(v is not None for v in m)  # the rest of the sky is still judged


def test_protocol_bounds() -> None:
    base = {"at": NEW_MOON, "night_id": "20261010"}
    with pytest.raises(ValidationError):
        SkyMetrics.model_validate(base | {"cloud_map": [0.0] * 23})
    with pytest.raises(ValidationError):
        SkyMetrics.model_validate(base | {"cloud_map": [1.5] + [None] * 23})
    assert SkyMetrics.model_validate(base).cloud_map is None
