"""Sun and moon (roadmap C1, L20)."""

from __future__ import annotations

import datetime as dt

from allskyhub_sky import astro

VIENNA = (48.2, 16.4)


def test_moon_phase() -> None:
    assert astro.moon_illumination(dt.datetime(2026, 10, 10, tzinfo=dt.UTC)) < 0.05  # new
    assert astro.moon_illumination(dt.datetime(2026, 10, 26, tzinfo=dt.UTC)) > 0.95  # full
    assert astro.moon_waxing(dt.datetime(2026, 10, 18, tzinfo=dt.UTC))


def test_bright_moon_up() -> None:
    # Full moon on 26 October 2026: up around local midnight, below the horizon at noon.
    midnight = dt.datetime(2026, 10, 25, 23, 0, tzinfo=dt.UTC)
    noon = dt.datetime(2026, 10, 26, 11, 0, tzinfo=dt.UTC)
    assert astro.moon_altitude(midnight, *VIENNA) > 30
    assert astro.bright_moon_up(midnight, *VIENNA)
    assert not astro.bright_moon_up(noon, *VIENNA)
    # New moon: never bright, even when it is up.
    new = dt.datetime(2026, 10, 10, 11, 0, tzinfo=dt.UTC)
    assert astro.moon_altitude(new, *VIENNA) > 0
    assert not astro.bright_moon_up(new, *VIENNA)


def test_sun() -> None:
    noon = dt.datetime(2026, 6, 21, 11, 0, tzinfo=dt.UTC)
    assert 63 < astro.sun_altitude(noon, *VIENNA) < 66
