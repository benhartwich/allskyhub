"""Sun position (SPEC §4.2), NOAA solar calculator approximation.

Accurate to well under 0.1° for the years this code will run, which is far more than the
day/night decision needs. Pure function: the time is a parameter.
"""

from __future__ import annotations

import math
from datetime import datetime


def _julian_day(t: datetime) -> float:
    if t.tzinfo is None:
        raise ValueError("time must be timezone-aware")
    return t.timestamp() / 86400.0 + 2440587.5


def sun_elevation(t: datetime, lat: float, lon: float) -> float:
    """Geometric elevation of the sun's centre in degrees, without refraction.

    `lat` is north-positive, `lon` east-positive, both in degrees.
    """
    return sun_position(t, lat, lon)[0]


def sun_position(t: datetime, lat: float, lon: float) -> tuple[float, float]:
    """(elevation, azimuth) of the sun's centre in degrees; azimuth from north, east = 90."""
    jc = (_julian_day(t) - 2451545.0) / 36525.0

    mean_lon = (280.46646 + jc * (36000.76983 + jc * 0.0003032)) % 360.0
    mean_anom = 357.52911 + jc * (35999.05029 - 0.0001537 * jc)
    ecc = 0.016708634 - jc * (0.000042037 + 0.0000001267 * jc)
    m = math.radians(mean_anom)
    centre = (
        math.sin(m) * (1.914602 - jc * (0.004817 + 0.000014 * jc))
        + math.sin(2 * m) * (0.019993 - 0.000101 * jc)
        + math.sin(3 * m) * 0.000289
    )
    true_lon = mean_lon + centre
    omega = 125.04 - 1934.136 * jc
    app_lon = true_lon - 0.00569 - 0.00478 * math.sin(math.radians(omega))

    mean_obliq = (
        23.0 + (26.0 + (21.448 - jc * (46.815 + jc * (0.00059 - jc * 0.001813))) / 60.0) / 60.0
    )
    obliq = math.radians(mean_obliq + 0.00256 * math.cos(math.radians(omega)))
    decl = math.asin(math.sin(obliq) * math.sin(math.radians(app_lon)))

    y = math.tan(obliq / 2) ** 2
    l0 = math.radians(mean_lon)
    eq_time = 4 * math.degrees(
        y * math.sin(2 * l0)
        - 2 * ecc * math.sin(m)
        + 4 * ecc * y * math.sin(m) * math.cos(2 * l0)
        - 0.5 * y * y * math.sin(4 * l0)
        - 1.25 * ecc * ecc * math.sin(2 * m)
    )  # minutes

    utc_minutes = (t.timestamp() % 86400.0) / 60.0
    true_solar = (utc_minutes + eq_time + 4 * lon) % 1440.0
    hour_angle = true_solar / 4 - 180 if true_solar >= 0 else true_solar / 4 + 180

    phi = math.radians(lat)
    cos_zenith = math.sin(phi) * math.sin(decl) + math.cos(phi) * math.cos(decl) * math.cos(
        math.radians(hour_angle)
    )
    zen = math.acos(max(-1.0, min(1.0, cos_zenith)))
    denom = math.cos(phi) * math.sin(zen)
    if abs(denom) < 1e-9:
        az = 180.0 if lat > math.degrees(decl) else 0.0  # sun at the zenith or a pole
    else:
        c = (math.sin(phi) * math.cos(zen) - math.sin(decl)) / denom
        a = math.degrees(math.acos(max(-1.0, min(1.0, c))))
        az = (a + 180.0) % 360.0 if hour_angle > 0 else (540.0 - a) % 360.0
    return 90.0 - math.degrees(zen), az
