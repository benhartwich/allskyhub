"""Fireballs seen by two cameras and where meteorites could have come down.

A meteor whose glowing path ends low in the atmosphere (``METEORITE_END_KM``) may have
dropped meteorites. From the triangulated path (devices/meteor_pairs.py) the hub draws a
rough fall area: a strip along the direction of flight beyond the end of the glow. Small
pieces stop quickly and fall almost straight down; heavy ones fly on for a while (dark
flight). Without the speed (an exposure does not give it) and without the winds aloft this
is only a first hint where to look, never a search area; experts (IMO, national fireball
networks) refine it.

Pairs of cameras whose owners joined the network (``Device.network``) are also listed
publicly on /feuerkugeln: path and fall area only, never where the cameras stand.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from allskyhub_server.models import MeteorPair

R_KM = 6371.0
# Meteorites come from fireballs whose glow ends below about 30–35 km; higher ones burn up.
METEORITE_END_KM = 35.0
# Peak brightness (SPEC §6.4 `peak`, 0..1) from which a meteor counts as a fireball,
# as for the IMO form (devices/science.py).
FIREBALL_PEAK = 0.9
# Dark flight beyond the end of the glow, as a part of the horizontal way a straight line
# would go to the ground: light pieces stop at once, heavy ones fly on (rule of thumb).
LIGHT_SHARE, HEAVY_SHARE = 0.1, 0.5
MAX_DARK_FLIGHT_KM = 40.0
# Half the width of the strip: the unknown wind drifts the falling pieces sideways.
BASE_HALF_WIDTH_KM = 2.0
WIND_PER_KM_HEIGHT = 0.1


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R_KM * math.asin(min(1.0, math.sqrt(h)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.atan2(y, x)) % 360


def destination(lat: float, lon: float, bearing: float, km: float) -> tuple[float, float]:
    p1, l1 = math.radians(lat), math.radians(lon)
    b, d = math.radians(bearing), km / R_KM
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(
        math.sin(b) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2)
    )
    return math.degrees(p2), (math.degrees(l2) + 540) % 360 - 180


@dataclass(frozen=True)
class FallArea:
    """A strip on the ground from ``near`` (light pieces) to ``far`` (heavy ones)."""

    end_km: float
    azimuth_deg: float  # direction of flight, from north towards east
    slope_deg: float  # angle of the path below the horizontal
    near: tuple[float, float]
    far: tuple[float, float]
    half_width_km: float

    def outline(self) -> list[tuple[float, float]]:
        """The strip as a closed polygon (lat, lon), for a map."""
        left, right = (self.azimuth_deg - 90) % 360, (self.azimuth_deg + 90) % 360
        w = self.half_width_km
        return [
            destination(*self.near, left, w),
            destination(*self.far, left, w),
            destination(*self.far, right, w),
            destination(*self.near, right, w),
            destination(*self.near, left, w),
        ]


def is_fireball(pair: MeteorPair) -> bool:
    return pair.low_km <= METEORITE_END_KM or (pair.peak or 0.0) >= FIREBALL_PEAK


def fall_area(pair: MeteorPair) -> FallArea | None:
    """None when the glow ended too high for meteorites."""
    if pair.low_km > METEORITE_END_KM:
        return None
    ground = distance_km(pair.high_lat, pair.high_lon, pair.low_lat, pair.low_lon)
    drop = max(pair.high_km - pair.low_km, 0.1)
    slope = math.degrees(math.atan2(drop, ground))
    azimuth = bearing_deg(pair.high_lat, pair.high_lon, pair.low_lat, pair.low_lon)
    # Where the straight path would meet the ground, measured from below its end.
    straight = pair.low_km * ground / drop
    near_km = min(LIGHT_SHARE * straight, MAX_DARK_FLIGHT_KM)
    far_km = max(min(HEAVY_SHARE * straight, MAX_DARK_FLIGHT_KM), near_km + 2.0)
    return FallArea(
        end_km=pair.low_km,
        azimuth_deg=azimuth,
        slope_deg=slope,
        near=destination(pair.low_lat, pair.low_lon, azimuth, near_km),
        far=destination(pair.low_lat, pair.low_lon, azimuth, far_km),
        half_width_km=BASE_HALF_WIDTH_KM + WIND_PER_KM_HEIGHT * pair.low_km,
    )


COMPASS = (
    "nördlich", "nordöstlich", "östlich", "südöstlich",
    "südlich", "südwestlich", "westlich", "nordwestlich",
)  # fmt: skip


def compass(azimuth: float) -> str:
    return COMPASS[round(azimuth / 45) % 8]


HEADINGS = (
    "Norden",
    "Nordosten",
    "Osten",
    "Südosten",
    "Süden",
    "Südwesten",
    "Westen",
    "Nordwesten",
)


def heading(azimuth: float) -> str:
    return HEADINGS[round(azimuth / 45) % 8]
