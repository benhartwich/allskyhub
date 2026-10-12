"""The shared sky drawing (SPEC §4.8 lens mapping)."""

from __future__ import annotations

import datetime as dt

from allskyhub_protocol import Lens
from allskyhub_sky import alt_az, overlay, precess, project, sidereal_deg, tilt, usable

AT = dt.datetime(2026, 10, 15, 21, tzinfo=dt.UTC)
LENS = Lens(cx=600, cy=600, a1=560, a3=-30, tilt_east_deg=0, tilt_north_deg=0,
            width=1200, height=1200)  # fmt: skip


def test_lens_mapping() -> None:
    assert project(90, 0, LENS, 0, True) == (600, 600)  # zenith in the centre
    x, y = project(0, 0, LENS, 0, True)  # north at the horizon: straight up
    assert (round(x, 6), round(y, 6)) == (600, 600 - (560 - 30))
    x, _ = project(0, 90, LENS, 0, True)  # mirrored: east to the left
    assert x < 600
    x, _ = project(0, 90, LENS, 0, False)
    assert x > 600


def test_tilt_moves_the_zenith_toward_its_lean() -> None:
    alt, _ = tilt(90, 0, 2.0, 0.0)
    assert abs(alt - 88.0) < 1e-9


def test_polaris_stands_at_the_latitude() -> None:
    alt, az = alt_az(*precess(37.95, 89.26, AT), sidereal_deg(AT, 14.39), 48.14)
    assert abs(alt - 48.14) < 1.0
    assert az < 2 or az > 358


def test_overlay() -> None:
    assert usable(LENS)
    assert not usable(LENS.model_copy(update={"width": 0}))
    assert not usable(None)
    drawn = overlay(LENS, 0.0, True, 48.14, 14.39, AT)
    assert drawn.paths
    assert (drawn.width, drawn.height) == (1200, 1200)
    assert {label.text for label in drawn.cardinals} == {"N", "O", "S", "W"}
    assert "Pegasus" in {label.text for label in drawn.names}  # high in an October night
