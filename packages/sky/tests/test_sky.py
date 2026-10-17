"""The shared sky drawing (SPEC §4.8 lens mapping)."""

from __future__ import annotations

import datetime as dt

from allskyhub_protocol import Lens
from allskyhub_sky import alt_az, overlay, precess, project, sidereal_deg, sky_lines, tilt, usable

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
    alt, az = alt_az(*precess(37.95, 89.26, AT), sidereal_deg(AT, 10.39), 50.04)
    assert abs(alt - 50.04) < 1.0
    assert az < 2 or az > 358


def test_overlay() -> None:
    assert usable(LENS)
    assert not usable(LENS.model_copy(update={"width": 0}))
    assert not usable(None)
    drawn = overlay(LENS, 0.0, True, 50.04, 10.39, AT)
    assert drawn.paths
    assert (drawn.width, drawn.height) == (1200, 1200)
    assert {label.text for label in drawn.cardinals} == {"N", "O", "S", "W"}
    assert "Pegasus" in {label.text for label in drawn.names}  # high in an October night


def test_cardinals_stay_in_a_cropped_frame() -> None:
    """A 16:9 frame cuts off the horizon at top and bottom: N and S go to the edge, in
    their true direction from the centre."""
    wide = Lens(cx=1920, cy=1080, a1=2010, a3=0, tilt_east_deg=0, tilt_north_deg=0,
                width=3840, height=2160)  # fmt: skip
    drawn = overlay(wide, 0.0, True, 50.04, 10.39, AT)
    margin = drawn.font
    found = {label.text: label for label in drawn.cardinals}
    for label in drawn.cardinals:
        assert margin - 1e-6 <= label.x <= wide.width - margin + 1e-6
        assert margin - 1e-6 <= label.y <= wide.height - margin + 1e-6
    assert abs(found["N"].x - 1920) < 1e-6
    assert abs(found["N"].y - margin) < 1e-6
    assert abs(found["S"].y - (wide.height - margin)) < 1e-6
    # a1 > cx: O and W are just outside too (mirrored: O on the left), on the centre line.
    assert abs(found["O"].x - margin) < 1e-6
    assert abs(found["O"].y - project(3.0, 90, wide, 0.0, True)[1]) < 1.0
    # Inside the frame nothing moves.
    square = overlay(LENS, 0.0, True, 50.04, 10.39, AT)
    assert square.cardinals[0].y == project(3.0, 0.0, LENS, 0.0, True)[1]


def test_sky_lines_are_directions_above_the_horizon() -> None:
    sky = sky_lines(50.04, 10.39, AT)
    assert sky.lines
    assert all(alt >= -5 and 0 <= az < 360 for line in sky.lines for alt, az in line)
    names = {name: (alt, az) for name, alt, az in sky.names}
    assert names["Pegasus"][0] > 40  # high in an October night
    # The same place in the sky as the image overlay uses.
    assert all(len(line) > 1 for line in sky.lines)
