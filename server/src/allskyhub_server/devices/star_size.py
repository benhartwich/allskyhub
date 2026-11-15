"""Star size (SPEC §6.3 `hfr`; draft E, approved 2026-11-15): a camera's usual value from
its clear nights (``NightStat.median_hfr``) and whether it grew over weeks.

The usual value is what the page shows next to the current one and what the dome hint
(devices/health.blur_hint) compares with. A new camera, or one that has not sent `hfr`
yet, has none: then nothing is compared.
"""

from __future__ import annotations

import statistics
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from allskyhub_server.models import Device, NightStat

USUAL_NIGHTS = 14
USUAL_MIN = 3
# The focus check: the newest DRIFT_RECENT clear nights against up to DRIFT_BEFORE before.
DRIFT_RECENT = 7
DRIFT_BEFORE = 30
DRIFT_MIN_BEFORE = 5
FACTOR = 1.5  # this much larger counts as blurred (to be checked on real data)
RELEASE = 1.3  # a hint already given goes only below this (no flapping around FACTOR)


def camera_key(device: Device) -> str | None:
    """What the star size depends on besides the sky: the profile and the camera the
    status names (sensor and resolution). Another camera starts a new usual value."""
    settings = (device.last_status or {}).get("settings")
    camera = cast(dict[str, Any], settings).get("camera") if isinstance(settings, dict) else None
    if not isinstance(camera, str) or not camera:
        return None
    return f"{device.profile}|{camera}"[:160]


async def medians(
    db: AsyncSession,
    device_id: str,
    camera: str | None,
    limit: int,
    before_night: str | None = None,
) -> list[float]:
    """The clear nights' star sizes measured by ``camera``, newest first, optionally only
    before a night; none without a camera."""
    if camera is None:
        return []
    query = select(NightStat.median_hfr).where(
        NightStat.device_id == device_id,
        NightStat.hfr_camera == camera,
        NightStat.median_hfr.is_not(None),
    )
    if before_night is not None:
        query = query.where(NightStat.night_id < before_night)
    rows = await db.scalars(query.order_by(NightStat.night_id.desc()).limit(limit))
    return [float(v) for v in rows if v is not None]


def usual_of(values: list[float]) -> float | None:
    return round(statistics.median(values), 2) if len(values) >= USUAL_MIN else None


async def usual(db: AsyncSession, device: Device, before_night: str | None) -> float | None:
    """The median of the camera's last ``USUAL_NIGHTS`` clear nights before
    ``before_night`` (the night going on is left out: it may be the blurred one)."""
    return usual_of(await medians(db, device.id, camera_key(device), USUAL_NIGHTS, before_night))


async def usual_now(db: AsyncSession, device: Device) -> float | None:
    """The usual value next to the camera's current star size; None (no query) while the
    camera reports none."""
    sky = (device.last_status or {}).get("sky")
    if not isinstance(sky, dict):
        return None
    sky = cast(dict[str, Any], sky)
    if sky.get("hfr") is None:
        return None
    night = sky.get("night_id")
    return await usual(db, device, night if isinstance(night, str) else None)


def drift(newest_first: list[float]) -> tuple[float, float] | None:
    """(recent, before) when the recent nights' stars are ``FACTOR`` times as large."""
    recent = newest_first[:DRIFT_RECENT]
    before = newest_first[DRIFT_RECENT : DRIFT_RECENT + DRIFT_BEFORE]
    if len(recent) < DRIFT_RECENT or len(before) < DRIFT_MIN_BEFORE:
        return None
    now, then = statistics.median(recent), statistics.median(before)
    if then <= 0 or now < FACTOR * then:
        return None
    return round(now, 2), round(then, 2)
