"""Map of public cameras (roadmap C5).

Only cameras with a public page whose owner shows coordinates, at the same rounding as on
the page (0.1°). The map is the hub's own Natural Earth outline (static/world-land.svg,
equirectangular, 10 px per degree), so no map service learns about visitors.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from allskyhub_server.devices import public
from allskyhub_server.models import Device

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

    @property
    def x(self) -> float:
        return (self.lon + 180) * 10

    @property
    def y(self) -> float:
        return (90 - self.lat) * 10


async def cameras(db: AsyncSession) -> list[MapCamera]:
    rows = await db.scalars(
        select(Device).where(Device.public_slug.is_not(None), Device.owner_id.is_not(None))
    )
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
            )
        )
    return sorted(out, key=lambda c: c.title.lower())
