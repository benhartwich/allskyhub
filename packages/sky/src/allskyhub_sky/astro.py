"""Sun and moon positions, low precision, no dependency (roadmap C1, L20).

Shared by the hub (observing forecast) and the camera (moon mode, SPEC §4.3). Positions
follow the short series in Meeus, "Astronomical Algorithms" (ch. 25 for the sun, ch. 47
with the largest terms for the moon): good to a few arc minutes for the sun and about a
quarter degree for the moon.
"""

from __future__ import annotations

import datetime as dt
import math

_J2000 = dt.datetime(2000, 1, 1, 12, tzinfo=dt.UTC)


def _days(t: dt.datetime) -> float:
    """Days since J2000.0 (UT; the difference to TT does not matter here)."""
    return (t - _J2000).total_seconds() / 86400


def _sin(deg: float) -> float:
    return math.sin(math.radians(deg))


def _cos(deg: float) -> float:
    return math.cos(math.radians(deg))


def _obliquity(d: float) -> float:
    return 23.439291 - 0.0000004 * d


def _equatorial(lon: float, lat: float, d: float) -> tuple[float, float]:
    """Ecliptic longitude/latitude → right ascension, declination (degrees)."""
    eps = _obliquity(d)
    ra = math.degrees(
        math.atan2(_sin(lon) * _cos(eps) - math.tan(math.radians(lat)) * _sin(eps), _cos(lon))
    )
    dec = math.degrees(math.asin(_sin(lat) * _cos(eps) + _cos(lat) * _sin(eps) * _sin(lon)))
    return ra % 360, dec


def _sun_ecliptic(d: float) -> float:
    g = (357.529 + 0.98560028 * d) % 360  # mean anomaly
    q = (280.459 + 0.98564736 * d) % 360  # mean longitude
    return (q + 1.915 * _sin(g) + 0.020 * _sin(2 * g)) % 360


def _moon_ecliptic(d: float) -> tuple[float, float, float]:
    """Longitude, latitude (degrees) and distance (km)."""
    t = d / 36525
    lp = 218.3164477 + 481267.88123421 * t  # mean longitude
    m_moon = 134.9633964 + 477198.8675055 * t  # moon's mean anomaly
    m_sun = 357.5291092 + 35999.0502909 * t  # sun's mean anomaly
    elong = 297.8501921 + 445267.1114034 * t  # mean elongation
    f = 93.2720950 + 483202.0175233 * t  # argument of latitude
    lon = (
        lp
        + 6.289 * _sin(m_moon)
        + 1.274 * _sin(2 * elong - m_moon)
        + 0.658 * _sin(2 * elong)
        + 0.214 * _sin(2 * m_moon)
        - 0.186 * _sin(m_sun)
        - 0.114 * _sin(2 * f)
        + 0.059 * _sin(2 * elong - 2 * m_moon)
        + 0.057 * _sin(2 * elong - m_sun - m_moon)
        + 0.053 * _sin(2 * elong + m_moon)
        + 0.046 * _sin(2 * elong - m_sun)
    )
    lat = (
        5.128 * _sin(f)
        + 0.281 * _sin(m_moon + f)
        + 0.278 * _sin(m_moon - f)
        + 0.173 * _sin(2 * elong - f)
    )
    dist = 385001 - 20905 * _cos(m_moon) - 3699 * _cos(2 * elong - m_moon) - 2956 * _cos(2 * elong)
    return lon % 360, lat, dist


def _altitude(ra: float, dec: float, t: dt.datetime, lat: float, lon: float) -> float:
    d = _days(t)
    gmst = (280.46061837 + 360.98564736629 * d) % 360
    ha = gmst + lon - ra
    return math.degrees(math.asin(_sin(lat) * _sin(dec) + _cos(lat) * _cos(dec) * _cos(ha)))


def sun_radec(t: dt.datetime) -> tuple[float, float]:
    """Right ascension and declination of the sun (degrees, equinox of date)."""
    d = _days(t)
    return _equatorial(_sun_ecliptic(d), 0.0, d)


def sun_altitude(t: dt.datetime, lat: float, lon: float) -> float:
    ra, dec = sun_radec(t)
    return _altitude(ra, dec, t, lat, lon)


def moon_radec(t: dt.datetime) -> tuple[float, float]:
    """Geocentric right ascension and declination of the moon (degrees, equinox of date)."""
    d = _days(t)
    mlon, mlat, _ = _moon_ecliptic(d)
    return _equatorial(mlon, mlat, d)


def moon_altitude(t: dt.datetime, lat: float, lon: float) -> float:
    """Topocentric (parallax applied), centre of the disc, without refraction."""
    d = _days(t)
    mlon, mlat, dist = _moon_ecliptic(d)
    ra, dec = _equatorial(mlon, mlat, d)
    geo = _altitude(ra, dec, t, lat, lon)
    parallax = math.degrees(math.asin(6378.14 / dist))
    return geo - parallax * _cos(geo)


def moon_illumination(t: dt.datetime) -> float:
    """Illuminated fraction of the disc, 0 (new) to 1 (full)."""
    d = _days(t)
    mlon, mlat, _ = _moon_ecliptic(d)
    slon = _sun_ecliptic(d)
    elongation = math.degrees(math.acos(_cos(mlat) * _cos(mlon - slon)))
    return (1 - _cos(elongation)) / 2


def moon_waxing(t: dt.datetime) -> bool:
    mlon, _, _ = _moon_ecliptic(_days(t))
    return (mlon - _sun_ecliptic(_days(t))) % 360 < 180


def bright_moon_up(t: dt.datetime, lat: float, lon: float, min_lit: float = 0.5) -> bool:
    """The moon above the horizon and at least `min_lit` lit (SPEC §4.3 moon mode)."""
    return moon_altitude(t, lat, lon) > 0 and moon_illumination(t) >= min_lit
