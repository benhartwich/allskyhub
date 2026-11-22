"""Statistics per month and year (roadmap C7) from ``NightStat``.

The maintenance loop keeps ``NightStat`` up to date from the samples and events while they
exist; the statistics then outlive them.
"""

from __future__ import annotations

import datetime as dt
import statistics
from dataclasses import dataclass, field

from sqlalchemy import case, func, select, union
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from allskyhub_server.devices import bright_satellites, brightness, star_size
from allskyhub_server.devices.event_text import shower_name
from allskyhub_server.devices.night_summary import summarize
from allskyhub_server.i18n import english
from allskyhub_server.models import Device, EventRecord, NightStat, SkySample

# Nights this recent are recomputed on every pass (late events, late measurements).
RECOMPUTE_DAYS = 3
CLEAR_NIGHT_HOURS = 2.0
MONTHS = ("Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez")
MONTHS_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


async def refresh(db: AsyncSession, now: dt.datetime) -> int:
    """Compute missing and recent nights; returns how many. Commits."""
    recent = (now - dt.timedelta(days=RECOMPUTE_DAYS)).strftime("%Y%m%d")
    nights = union(
        select(SkySample.device_id, SkySample.night_id),
        select(EventRecord.device_id, EventRecord.night_id),
    ).subquery()
    known = {
        (d, n) for d, n in (await db.execute(select(NightStat.device_id, NightStat.night_id))).all()
    }
    rows = (
        await db.execute(
            select(nights.c.device_id, nights.c.night_id)
            .join(Device, Device.id == nights.c.device_id)
            .where(Device.owner_id.is_not(None))
        )
    ).all()
    todo = [(d, n) for d, n in rows if n >= recent or (d, n) not in known]
    for device_id, night_id in todo:
        await refresh_night(db, device_id, night_id)
    await db.commit()
    return len(todo)


async def refresh_night(db: AsyncSession, device_id: str, night_id: str) -> None:
    """One night in numbers, again (also after a label changed, SPEC §6.4: a false
    positive counts nowhere). The caller commits."""
    s = await summarize(db, device_id, night_id)
    device = await db.get(Device, device_id)
    sky_map = await _brightness(db, device, night_id) if device else None
    values: dict[str, object] = {
        "brightness_map": sky_map.values if sky_map else None,
        "brightness_map_flat": sky_map.flat if sky_map else None,
        "clear_hours": s.clear_hours,
        "best_sqm": s.best_sqm,
        "median_hfr": s.median_hfr,
        "meteors": s.meteor_count,
        "showers": s.showers,
        "aurora": s.aurora,
        "storms": s.storms,
    }
    insert = pg_insert(NightStat).values(
        device_id=device_id,
        night_id=night_id,
        hfr_camera=star_size.camera_key(device) if device else None,
        **values,
    )
    # The camera of the night's first pass stays (a later camera change must not relabel
    # the nights still recomputed).
    camera = func.coalesce(NightStat.hfr_camera, insert.excluded.hfr_camera)
    # A label change recomputes a night weeks later, when its samples have expired
    # (maintenance, keep_thumb_days): what came from the samples then stays as it was
    # (review of #500). The map and its correction go together.
    new_map = insert.excluded.brightness_map
    kept: dict[str, object] = {
        name: func.coalesce(insert.excluded[name], NightStat.__table__.c[name])
        for name in ("clear_hours", "best_sqm", "median_hfr", "brightness_map")
    }
    kept["brightness_map_flat"] = case(
        (new_map.is_(None), NightStat.brightness_map_flat),
        else_=insert.excluded.brightness_map_flat,
    )
    await db.execute(
        insert.on_conflict_do_update(
            index_elements=["device_id", "night_id"],
            set_={**values, **kept, "hfr_camera": camera},
        )
    )


async def _brightness(
    db: AsyncSession, device: Device, night_id: str
) -> brightness.NightMap | None:
    """L37: the night's sky brightness per cell; needs the place for the moon."""
    where = bright_satellites.location(device.last_status)
    if where is None:
        return None
    samples = await db.scalars(
        select(SkySample).where(
            SkySample.device_id == device.id,
            SkySample.night_id == night_id,
            SkySample.brightness_map.is_not(None),
        )
    )
    return brightness.night_map(samples, *where)


@dataclass
class Period:
    label: str
    nights: int = 0  # nights with any data
    clear_nights: int = 0  # at least CLEAR_NIGHT_HOURS clear
    clear_hours: float = 0.0
    best_sqm: float | None = None
    sizes: list[float] = field(default_factory=list[float])  # draft E: clear nights' HFR
    # L37: the nights' sky brightness maps with their correction.
    maps: list[tuple[list[float | None], str | None]] = field(
        default_factory=list[tuple[list[float | None], str | None]]
    )
    meteors: int = 0
    aurora: int = 0
    storms: int = 0

    def add(self, n: NightStat) -> None:
        self.nights += 1
        if n.clear_hours is not None:
            self.clear_hours += n.clear_hours
            self.clear_nights += n.clear_hours >= CLEAR_NIGHT_HOURS
        if n.best_sqm is not None:
            self.best_sqm = max(self.best_sqm or n.best_sqm, n.best_sqm)
        if n.median_hfr is not None:
            self.sizes.append(n.median_hfr)
        if n.brightness_map is not None:
            self.maps.append((n.brightness_map, n.brightness_map_flat))
        self.meteors += n.meteors
        self.aurora += n.aurora
        self.storms += n.storms

    @property
    def brightness(self) -> brightness.Chart | None:
        """L37: the period's sky brightness per cell (median of its night maps)."""
        found = brightness.combine(self.maps)
        if found is None:
            return None
        return brightness.chart(self.label, found[0], found[1], english())

    @property
    def hfr(self) -> float | None:
        """The median star size of the period's clear nights, px."""
        return round(statistics.median(self.sizes), 2) if self.sizes else None


@dataclass
class Year:
    year: int
    months: list[Period]
    total: Period
    showers: dict[str, int] = field(default_factory=dict[str, int])
    years: list[int] = field(default_factory=list[int])  # years with data, newest first

    @property
    def has_hfr(self) -> bool:
        return any(m.sizes for m in self.months)

    @property
    def brightness_months(self) -> list[tuple[str, brightness.Chart]]:
        """L37: the months with a sky brightness map, for the small charts."""
        out: list[tuple[str, brightness.Chart]] = []
        for m in self.months:
            found = m.brightness
            if found is not None:
                out.append((m.label, found))
        return out

    @property
    def max_clear_hours(self) -> float:
        return max((m.clear_hours for m in self.months), default=0.0) or 1.0


async def year(db: AsyncSession, device_id: str, year: int) -> Year:
    """The night id is the evening's date (SPEC §4.5), so a night counts for its evening."""
    rows = list(
        await db.scalars(
            select(NightStat).where(
                NightStat.device_id == device_id,
                NightStat.night_id >= f"{year}0101",
                NightStat.night_id <= f"{year}1231",
            )
        )
    )
    months = [Period(name) for name in (MONTHS_EN if english() else MONTHS)]
    total = Period(str(year))
    showers: dict[str, int] = {}
    for n in rows:
        months[int(n.night_id[4:6]) - 1].add(n)
        total.add(n)
        for key, count in (n.showers or {}).items():
            name = shower_name(key)  # stored as the camera names it (SPEC §6.4)
            showers[name] = showers.get(name, 0) + int(count)
    years = sorted(
        {
            int(y[:4])
            for y in await db.scalars(
                select(NightStat.night_id).where(NightStat.device_id == device_id)
            )
        },
        reverse=True,
    )
    ordered = dict(sorted(showers.items(), key=lambda kv: -kv[1]))
    return Year(year, months, total, ordered, years)
