"""Winds aloft for a fireball's dark flight (package C), from NOAA's GFS.

GFS (0.25°, public domain) has pressure levels up to 1 hPa, about 48 km, above the end of
any glow; DWD's ICON-EU stops at 50 hPa, about 20 km (Benjamin, 2026-11-05). NOMADS cuts a
small box around the end of the glow out of one forecast: wind east and north and the
geopotential height on 22 levels, some 20 kB. The run is the newest one started before the
meteor whose files are out, the step the hour nearest to it; NOMADS keeps about ten days.

Every ``EVERY`` the hub looks for fireballs that may have dropped meteorites and have no
winds yet, takes the winds above the end of the glow (weighted by distance from the four
nearest grid points) and keeps them with the meteor's pairs, together with the strewn
field they give (devices/dark_flight.py), for the page and the map.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import importlib
import logging
import math
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices import dark_flight, fireball
from allskyhub_server.models import MeteorPair

log = logging.getLogger(__name__)

FILTER = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl"
SOURCE = "NOAA GFS 0,25°"
LEVELS_HPA = (1, 2, 3, 5, 7, 10, 20, 30, 50, 70, 100, 150, 200, 250, 300, 400, 500, 600, 700,
              850, 925, 1000)  # fmt: skip
EVERY = dt.timedelta(minutes=30)
KEEP = dt.timedelta(days=9)  # NOMADS keeps about ten days
CYCLE_H = 6
MAX_STEP_H = 120  # hourly steps up to here
BOX_DEG = 0.5
# NOMADS blocks above about 120 requests a minute: a round asks for few fireballs, one
# request at a time with a pause (up to 4 runs each).
PER_ROUND = 10
PAUSE_S = 1.0
TIMEOUT_S = 30.0


def runs(at: dt.datetime, now: dt.datetime) -> list[tuple[dt.datetime, int]]:
    """Runs to try, newest first: started before ``at`` and long enough ago that their
    files may be out (about 4 h), with the step nearest to ``at``."""
    start = at.replace(minute=0, second=0, microsecond=0)
    start -= dt.timedelta(hours=start.hour % CYCLE_H)
    out: list[tuple[dt.datetime, int]] = []
    for back in range(4):
        run = start - dt.timedelta(hours=CYCLE_H * back)
        step = round((at - run).total_seconds() / 3600)
        if run <= now - dt.timedelta(hours=4) and 0 <= step <= MAX_STEP_H:
            out.append((run, step))
    return out


def url(run: dt.datetime, step: int, lat: float, lon: float) -> str:
    levels = "".join(f"&lev_{p}_mb=on" for p in LEVELS_HPA)
    return (
        f"{FILTER}?dir=%2Fgfs.{run:%Y%m%d}%2F{run:%H}%2Fatmos"
        f"&file=gfs.t{run:%H}z.pgrb2.0p25.f{step:03d}&var_UGRD=on&var_VGRD=on&var_HGT=on"
        f"{levels}&subregion=&toplat={lat + BOX_DEG:.2f}&bottomlat={lat - BOX_DEG:.2f}"
        f"&leftlon={lon - BOX_DEG:.2f}&rightlon={lon + BOX_DEG:.2f}"
    )


def profile(grib: bytes, lat: float, lon: float) -> dark_flight.Profile:
    """The winds above (lat, lon) from a GFS cut: (height km, east, north m/s), ground up."""
    # Its typing is partial and not ours to fix; everything it gives is checked below.
    ec: Any = importlib.import_module("eccodes")

    levels: dict[int, dict[str, float]] = {}
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "gfs.grb2"
        path.write_bytes(grib)
        with path.open("rb") as f:
            while (h := ec.codes_grib_new_from_file(f)) is not None:
                try:
                    name = str(ec.codes_get(h, "shortName"))
                    level = int(ec.codes_get(h, "level"))
                    values = [float(v) for v in ec.codes_get_array(h, "values")]
                    lats = [float(v) for v in ec.codes_get_array(h, "latitudes")]
                    lons = [float(v) for v in ec.codes_get_array(h, "longitudes")]
                finally:
                    ec.codes_release(h)
                levels.setdefault(level, {})[name] = _at(values, lats, lons, lat, lon)
    rows = [
        (v["gh"] / 1000, v["u"], v["v"])
        for v in levels.values()
        if {"gh", "u", "v"} <= v.keys() and all(math.isfinite(x) for x in v.values())
    ]
    return sorted(rows)


def _at(values: list[float], lats: list[float], lons: list[float], lat: float, lon: float) -> float:
    """The value at (lat, lon) from the four nearest grid points, by inverse distance."""
    scale = math.cos(math.radians(lat))
    near = sorted(
        (math.hypot(la - lat, (((lo - lon + 180) % 360) - 180) * scale), v)
        for la, lo, v in zip(lats, lons, values, strict=True)
    )[:4]
    if near and near[0][0] < 1e-6:
        return near[0][1]
    weights = [1 / d for d, _ in near]
    return sum(w * v for w, (_, v) in zip(weights, near, strict=True)) / sum(weights)


async def fetch(
    client: httpx.AsyncClient, at: dt.datetime, lat: float, lon: float, now: dt.datetime
) -> tuple[dict[str, Any], dark_flight.Profile] | None:
    """The winds above the place at the time, or None when no run is out (yet)."""
    for run, step in runs(at, now):
        await asyncio.sleep(PAUSE_S)
        r = await client.get(url(run, step, lat, lon), timeout=TIMEOUT_S)
        if r.status_code == 404 or not r.content.startswith(b"GRIB"):
            continue
        r.raise_for_status()
        rows = await asyncio.to_thread(profile, r.content, lat, lon)
        if len(rows) >= 5:
            return {"source": SOURCE, "run": run.isoformat(), "step_h": step}, rows
    return None


def field_values(
    pair: MeteorPair, meta: dict[str, Any], rows: dark_flight.Profile
) -> dict[str, Any]:
    field = dark_flight.strewn_field(
        (pair.high_lat, pair.high_lon, pair.high_km),
        (pair.low_lat, pair.low_lon, pair.low_km),
        rows,
        pair.low_err_km or 0.0,
    )
    return {
        "wind": {**meta, "levels": [list(r) for r in rows]},
        "strewn": {
            "landings": [asdict(landing) for landing in field.landings],
            "outline": [list(p) for p in field.outline],
        },
    }


async def round_once(db: AsyncSession, client: httpx.AsyncClient, now: dt.datetime) -> int:
    """Winds for the fireballs still without; returns how many meteors got them."""
    pairs = (
        await db.scalars(
            select(MeteorPair)
            .where(
                MeteorPair.low_km <= fireball.METEORITE_END_KM,
                MeteorPair.wind.is_(None),
                MeteorPair.at >= now - KEEP,
            )
            .order_by(MeteorPair.at.desc())
            .limit(PER_ROUND)
        )
    ).all()
    done = 0
    for pair in pairs:
        if pair.wind is not None:  # the same meteor's other pair got them this round
            continue
        found = await fetch(client, pair.at, pair.low_lat, pair.low_lon, now)
        if found is None:
            continue
        values = await asyncio.to_thread(field_values, pair, *found)  # ~0.2 s of numbers
        await db.execute(
            update(MeteorPair)
            .where(
                MeteorPair.wind.is_(None),
                MeteorPair.at.between(pair.at - dt.timedelta(seconds=30),
                                      pair.at + dt.timedelta(seconds=30)),
                MeteorPair.low_lat.between(pair.low_lat - 0.05, pair.low_lat + 0.05),
                MeteorPair.low_lon.between(pair.low_lon - 0.05, pair.low_lon + 0.05),
            )
            .values(**values)
            .execution_options(synchronize_session="fetch")
        )  # fmt: skip
        await db.commit()
        done += 1
    return done


async def run_forever(maker: async_sessionmaker[AsyncSession], client: httpx.AsyncClient) -> None:
    while True:
        try:
            async with maker() as db:
                done = await round_once(db, client, dt.datetime.now(dt.UTC))
            if done:
                log.info("winds: %d fireballs got their winds aloft", done)
        except Exception:  # NOMADS unreachable or a bad file: the next round tries again
            log.exception("winds round failed")
        await asyncio.sleep(EVERY.total_seconds())
