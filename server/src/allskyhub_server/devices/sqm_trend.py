"""How a camera's sky brightness changes over the years (roadmap N5).

From each night's darkest measurement (``NightStat.best_sqm``, SPEC §6.3 `sqm_mag`) of
clear nights without much moon: the median per month, and the trend over all months as a
robust slope (Theil–Sen, the median of all pairwise slopes) in mag/″² per year. Negative
means the sky gets brighter, i.e. more light pollution. Seasons (the Milky Way overhead,
airglow, snow) shift single months; the trend needs at least ``MIN_SPAN_MONTHS`` of data.
The cameras are not calibrated against an SQM meter, so levels are rough; a trend from one
camera is more meaningful than the level itself.
"""

from __future__ import annotations

import datetime as dt
import statistics
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from allskyhub_server.i18n import english, tr
from allskyhub_server.models import Device, NightStat
from allskyhub_sky import astro

MIN_CLEAR_HOURS = 1.0
# Moon lit more than this at the night's midnight brightens the sky.
MAX_MOON = 0.35
MIN_NIGHTS_PER_MONTH = 2
MIN_SPAN_MONTHS = 12
MIN_MONTHS = 6
# Below this change per year the sky counts as unchanged.
STEADY = 0.03


@dataclass(frozen=True)
class Month:
    year: int
    month: int
    sqm: float  # median of the month's darkest values
    nights: int

    @property
    def index(self) -> int:
        return self.year * 12 + self.month - 1

    @property
    def label(self) -> str:
        return f"{self.month:02d}/{self.year}"


@dataclass(frozen=True)
class Trend:
    months: list[Month]
    per_year: float | None  # mag/″² per year, None when too little data

    @property
    def text(self) -> str:
        if self.per_year is None:
            return tr(
                "Für einen Trend braucht es Messungen über mindestens ein Jahr.",
                "A trend needs measurements over at least a year.",
            )
        value = f"{abs(self.per_year):.2f}"
        if not english():
            value = value.replace(".", ",")
        if abs(self.per_year) < STEADY:
            return tr(
                f"Der Himmel ist gleich dunkel geblieben ({value} mag/″² pro Jahr).",
                f"The sky has stayed equally dark ({value} mag/″² per year).",
            )
        if self.per_year < 0:
            return tr(
                f"Der Himmel wird heller: {value} mag/″² pro Jahr mehr Lichtverschmutzung.",
                f"The sky is getting brighter: {value} mag/″² more light pollution per year.",
            )
        return tr(
            f"Der Himmel wird dunkler: {value} mag/″² pro Jahr.",
            f"The sky is getting darker: {value} mag/″² per year.",
        )

    @property
    def latest(self) -> Month | None:
        return self.months[-1] if self.months else None


def _moonless(night_id: str) -> bool:
    evening = dt.date(int(night_id[:4]), int(night_id[4:6]), int(night_id[6:8]))
    midnight = dt.datetime.combine(evening + dt.timedelta(days=1), dt.time(), dt.UTC)
    return astro.moon_illumination(midnight) <= MAX_MOON


def monthly(nights: list[NightStat]) -> list[Month]:
    by_month: dict[tuple[int, int], list[float]] = {}
    for n in nights:
        if n.best_sqm is None or (n.clear_hours or 0.0) < MIN_CLEAR_HOURS:
            continue
        if not _moonless(n.night_id):
            continue
        key = (int(n.night_id[:4]), int(n.night_id[4:6]))
        by_month.setdefault(key, []).append(n.best_sqm)
    return [
        Month(y, m, statistics.median(values), len(values))
        for (y, m), values in sorted(by_month.items())
        if len(values) >= MIN_NIGHTS_PER_MONTH
    ]


def slope(months: list[Month]) -> float | None:
    """Theil–Sen slope in mag per year; None with too little data."""
    if len(months) < MIN_MONTHS or months[-1].index - months[0].index < MIN_SPAN_MONTHS:
        return None
    slopes = [
        (b.sqm - a.sqm) / (b.index - a.index) * 12
        for i, a in enumerate(months)
        for b in months[i + 1 :]
    ]
    return statistics.median(slopes)


async def for_device(db: AsyncSession, device: Device) -> Trend:
    nights = list(await db.scalars(select(NightStat).where(NightStat.device_id == device.id)))
    months = monthly(nights)
    return Trend(months, slope(months))
