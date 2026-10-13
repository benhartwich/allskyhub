"""What happened in a camera's night, in one line (roadmap C3).

Used for the "Letzte Nacht" card on the camera page and the morning push.
"""

from __future__ import annotations

import datetime as dt
import itertools
import statistics
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from allskyhub_server.devices import events as events_mod
from allskyhub_server.devices.event_text import EVENT_TITLES, meteor_summary, shower_name
from allskyhub_server.devices.events import storm_key
from allskyhub_server.models import EventRecord, SkySample

CLEAR_BELOW = 0.3  # cloud cover
# Pictures worth showing as "the night's best", rarest first.
_BEST_ORDER = ("aurora", "nlc", "meteor", "lightning")


@dataclass
class NightSummary:
    night_id: str
    meteors: str | None = None  # "3 Meteore: 2 Perseiden, 1 sporadisch"
    meteor_count: int = 0
    showers: dict[str, int] = field(default_factory=dict[str, int])  # radiant matches
    aurora: int = 0
    storms: int = 0
    others: list[str] = field(default_factory=list[str])  # "1 Polarlicht", "1 Gewitter"
    clear_hours: float | None = None
    best_sqm: float | None = None
    best: EventRecord | None = None

    @property
    def empty(self) -> bool:
        return not (self.meteors or self.others or self.clear_hours or self.best_sqm)

    def line(self) -> str:
        """For the push: the most important facts, comma-separated."""
        parts: list[str] = []
        if self.meteors:
            parts.append(self.meteors)
        parts += self.others
        if self.clear_hours is not None:
            parts.append(
                "keine klare Stunde"
                if self.clear_hours < 0.5
                else f"{_num(self.clear_hours)} h klar"
            )
        if self.best_sqm is not None:
            parts.append(f"beste Himmelshelligkeit {_num(self.best_sqm, 2)} mag/″²")
        return ", ".join(parts) if parts else "Keine Messungen aus dieser Nacht."


def _num(value: float, digits: int = 1) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


_OTHER_NAMES = {
    "aurora": ("Polarlicht", "Polarlichter"),
    "nlc": ("Leuchtende Nachtwolke", "Leuchtende Nachtwolken"),
}


async def summarize(db: AsyncSession, device_id: str, night_id: str) -> NightSummary:
    summary = NightSummary(night_id)
    events = list(
        await db.scalars(
            select(EventRecord).where(
                EventRecord.device_id == device_id,
                EventRecord.night_id == night_id,
                events_mod.counted(),
            )
        )
    )
    summary.meteors = meteor_summary(events)
    for e in events:
        if e.kind != "meteor":
            continue
        summary.meteor_count += 1
        if e.data.get("shower_match") == "radiant":
            shower = e.data.get("shower")
            name = shower_name(shower) if isinstance(shower, str) and shower else "sporadisch"
            summary.showers[name] = summary.showers.get(name, 0) + 1
    for kind, (one, many) in _OTHER_NAMES.items():
        n = sum(e.kind == kind for e in events)
        if kind == "aurora":
            summary.aurora = n
        if n:
            summary.others.append(_plural(n, one, many))
    storms = {storm_key(e) or e.event_id for e in events if e.kind == "lightning"}
    summary.storms = len(storms)
    if storms:
        summary.others.append(_plural(len(storms), "Gewitter", "Gewitter"))
    for kind in _BEST_ORDER:
        candidates = [e for e in events if e.kind == kind and e.has_thumb]
        if candidates:
            summary.best = max(candidates, key=lambda e: e.confidence)
            break

    samples = list(
        await db.scalars(
            select(SkySample)
            .where(SkySample.device_id == device_id, SkySample.night_id == night_id)
            .order_by(SkySample.at)
        )
    )
    clouds = [s for s in samples if s.cloud_cover is not None]
    if len(clouds) >= 2:
        gaps = [(b.at - a.at).total_seconds() for a, b in itertools.pairwise(clouds)]
        step = min(statistics.median(gaps), 1800.0)  # gaps longer than that are outages
        clear = sum(
            min((b.at - a.at).total_seconds(), step)
            for a, b in itertools.pairwise(clouds)
            if a.cloud_cover is not None and a.cloud_cover < CLEAR_BELOW
        )
        summary.clear_hours = clear / 3600
    sqms = [s.sqm_mag for s in samples if s.sqm_mag is not None]
    if sqms:
        summary.best_sqm = max(sqms)
    return summary


def title_of(event: EventRecord) -> str:
    return EVENT_TITLES.get(event.kind, event.kind)


def last_night_id(now: dt.datetime, zone: dt.tzinfo) -> str:
    """The night id (SPEC §4.5: the evening's date) of the night that ended this morning."""
    local = now.astimezone(zone)
    return (local.date() - dt.timedelta(days=1)).strftime("%Y%m%d")
