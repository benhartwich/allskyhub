"""Sun position (SPEC §4.2) against reference values."""

from datetime import UTC, datetime

import pytest

from allskyhub_agent.core.sun import sun_elevation


@pytest.mark.parametrize(
    ("t", "lat", "lon", "expected"),
    [
        # Equinox noon at Greenwich on the equator: sun near the zenith.
        (datetime(2026, 3, 20, 12, 7, tzinfo=UTC), 0.0, 0.0, 89.9),
        # June solstice, local solar noon at 48.14 N: 90 - 48.14 + 23.44.
        (datetime(2026, 6, 21, 11, 2, tzinfo=UTC), 48.14, 14.39, 65.3),
        # December solstice, solar noon at 48.14 N: 90 - 48.14 - 23.44.
        (datetime(2026, 12, 21, 10, 56, tzinfo=UTC), 48.14, 14.39, 18.4),
        # Linz, 2026-10-05 19:07 CEST: civil twilight just ended (checked against NOAA).
        (datetime(2026, 10, 5, 17, 7, tzinfo=UTC), 48.14, 14.39, -6.5),
    ],
)
def test_reference_elevations(t: datetime, lat: float, lon: float, expected: float) -> None:
    assert sun_elevation(t, lat, lon) == pytest.approx(expected, abs=0.4)


def test_midnight_is_below_horizon() -> None:
    assert sun_elevation(datetime(2026, 10, 6, 23, 0, tzinfo=UTC), 48.14, 14.39) < -30


def test_naive_time_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        sun_elevation(datetime(2026, 10, 6, 12, 0), 48.0, 14.0)  # noqa: DTZ001
