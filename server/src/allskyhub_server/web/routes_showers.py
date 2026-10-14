"""Meteor showers live from the camera network (roadmap N3, devices/showers.py)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response

from allskyhub_server.devices import showers
from allskyhub_server.web.deps import DbSession, OptionalSession
from allskyhub_server.web.render import render

router = APIRouter()


def ra_text(ra: float) -> str:
    hours = ra / 15
    h = int(hours)
    m = round((hours - h) * 60)
    if m == 60:
        h, m = (h + 1) % 24, 0
    return f"{h} h {m:02d} min"


def dec_text(dec: float) -> str:
    return f"{dec:+.0f}°".replace("-", "\u2212")  # a real minus sign


@router.get("/meteorstroeme")
async def meteor_showers(request: Request, db: DbSession, session: OptionalSession) -> Response:
    live = await showers.live(db)
    today = live.now.date()
    items = [
        {
            "shower": s,
            "radiant": showers.radiant(s, today),
            "days": showers.days_to_peak(s, today),
            "total": live.total(s.key),
            "measured": live.measured.get(s.key, []),
            "mean": live.mean(s.key),
        }
        for s in live.showers
    ]
    for item in items:
        mean = item["mean"]
        item["mean_off"] = showers.separation(mean, item["radiant"]) if mean else None  # pyright: ignore[reportArgumentType]
    peak = max((h.meteors for h in live.hours), default=0)
    return render(
        request,
        "showers.html",
        {
            "live": live,
            "items": items,
            "peak": peak,
            "ra_text": ra_text,
            "dec_text": dec_text,
        },
        session=session,
    )
