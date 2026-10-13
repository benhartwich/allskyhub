"""Map of public cameras (roadmap C5), how dark their sky is (roadmap H4), and the public
cameras on the start page.

Only cameras with a public page whose owner shows coordinates, at the same rounding as on
the page (0.1°). The map is the hub's own Natural Earth outline (static/world-land.svg,
equirectangular, 10 px per degree), so no map service learns about visitors.

The light-pollution view uses the cameras' own sky brightness (SPEC §6.3 `sqm_mag`): the
median of the darkest reading of each clear night in the last ``SQM_DAYS`` days, only for
cameras whose public page shows the sky quality. Approximate, like the readings.
"""

from __future__ import annotations

import datetime as dt
import statistics
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from allskyhub_server.devices import public
from allskyhub_server.models import Device, NightStat

SQM_DAYS = 60
SQM_MIN_NIGHTS = 3
SQM_CLEAR_HOURS = 1.0
# Lower bound (mag/arcsec²) → class name and label, darkest first; roughly the Bortle scale.
SQM_CLASSES = (
    (21.7, "sqm-1", "sehr dunkel (≥ 21,7)"),
    (21.3, "sqm-2", "dunkel (21,3–21,7)"),
    (20.8, "sqm-3", "ländlich (20,8–21,3)"),
    (20.0, "sqm-4", "Stadtrand (20,0–20,8)"),
    (19.0, "sqm-5", "Vorstadt (19,0–20,0)"),
    (0.0, "sqm-6", "Stadt (< 19,0)"),
)

# Name → (title, viewBox of world-land.svg, dot radius); x = (lon + 180) * 10,
# y = (90 - lat) * 10. The radius keeps dots the same size on screen.
REGIONS = {
    "welt": ("Welt", "0 0 3600 1800", 30),
    "europa": ("Europa", "1550 180 700 400", 6),
    "nordamerika": ("Nordamerika", "100 150 1300 600", 11),
}


@dataclass(frozen=True)
class MapCamera:
    slug: str
    title: str
    place: str
    lat: float
    lon: float
    has_image: bool
    sqm: float | None = None

    @property
    def sqm_class(self) -> str:
        if self.sqm is None:
            return "sqm-none"
        return next(name for low, name, _ in SQM_CLASSES if self.sqm >= low)

    @property
    def x(self) -> float:
        return (self.lon + 180) * 10

    @property
    def y(self) -> float:
        return (90 - self.lat) * 10


async def _sqm(db: AsyncSession, device_ids: list[str], now: dt.datetime) -> dict[str, float]:
    since = (now - dt.timedelta(days=SQM_DAYS)).strftime("%Y%m%d")
    rows = await db.execute(
        select(NightStat.device_id, NightStat.best_sqm).where(
            NightStat.device_id.in_(device_ids),
            NightStat.night_id >= since,
            NightStat.best_sqm.is_not(None),
            NightStat.clear_hours >= SQM_CLEAR_HOURS,
        )
    )
    values: dict[str, list[float]] = {}
    for device_id, sqm in rows.all():
        if sqm is not None:
            values.setdefault(device_id, []).append(sqm)
    return {
        d: round(statistics.median(v), 2) for d, v in values.items() if len(v) >= SQM_MIN_NIGHTS
    }


async def cameras(db: AsyncSession, now: dt.datetime | None = None) -> list[MapCamera]:
    rows = list(
        await db.scalars(
            select(Device).where(Device.public_slug.is_not(None), Device.owner_id.is_not(None))
        )
    )
    shows_sky = [d.id for d in rows if public.profile(d).sections.sky]
    sqm = await _sqm(db, shows_sky, now or dt.datetime.now(dt.UTC)) if shows_sky else {}
    out: list[MapCamera] = []
    for device in rows:
        coords = public.coordinates(device)
        if coords is None or device.public_slug is None:
            continue
        out.append(
            MapCamera(
                slug=device.public_slug,
                title=public.title(device),
                place=public.profile(device).location_label,
                lat=coords[0],
                lon=coords[1],
                has_image=device.latest_image_at is not None,
                sqm=sqm.get(device.id),
            )
        )
    return sorted(out, key=lambda c: c.title.lower())


HOME_MAX = 12


@dataclass(frozen=True)
class HomeCamera:
    slug: str
    title: str
    place: str
    image_at: dt.datetime | None


async def home_cameras(db: AsyncSession) -> list[HomeCamera]:
    """Public cameras whose owner lists them on the start page, the freshest image first."""
    rows = await db.scalars(
        select(Device)
        .where(
            Device.public_slug.is_not(None),
            Device.owner_id.is_not(None),
            Device.public_on_home.is_(True),
        )
        .order_by(Device.latest_image_at.desc().nulls_last(), Device.id)
        .limit(HOME_MAX)
    )
    return [
        HomeCamera(
            slug=device.public_slug,
            title=public.title(device),
            place=public.profile(device).location_label,
            image_at=device.latest_image_at,
        )
        for device in rows
        if device.public_slug is not None
    ]
