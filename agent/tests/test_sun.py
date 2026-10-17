"""Sun position (SPEC §4.2) against reference values."""

from datetime import UTC, datetime

import pytest

from allskyhub_agent.core.sun import sun_elevation


@pytest.mark.parametrize(
    ("t", "lat", "lon", "expected"),
    [
        # Equinox noon at Greenwich on the equator: sun near the zenith.
        (datetime(2026, 3, 20, 12, 7, tzinfo=UTC), 0.0, 0.0, 89.9),
        # June solstice, local solar noon at 50.04 N: 90 - 50.04 + 23.44.
        (datetime(2026, 6, 21, 11, 20, tzinfo=UTC), 50.04, 10.39, 63.4),
        # December solstice, solar noon at 50.04 N: 90 - 50.04 - 23.44.
        (datetime(2026, 12, 21, 11, 16, tzinfo=UTC), 50.04, 10.39, 16.5),
        # 2026-10-05 19:24 CEST: civil twilight just ended (checked against Skyfield).
        (datetime(2026, 10, 5, 17, 24, tzinfo=UTC), 50.04, 10.39, -6.5),
    ],
)
def test_reference_elevations(t: datetime, lat: float, lon: float, expected: float) -> None:
    assert sun_elevation(t, lat, lon) == pytest.approx(expected, abs=0.4)


def test_midnight_is_below_horizon() -> None:
    assert sun_elevation(datetime(2026, 10, 6, 23, 0, tzinfo=UTC), 50.04, 10.39) < -30


def test_naive_time_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        sun_elevation(datetime(2026, 10, 6, 12, 0), 48.0, 14.0)  # noqa: DTZ001
