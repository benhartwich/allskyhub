"""The camera's trail verdicts (roadmap L25, SPEC §6.4): counted nowhere, also before the
camera's automatic label arrives."""

from __future__ import annotations

import datetime as dt

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices import events, stats
from allskyhub_server.models import EventRecord, NightStat
from tests.fake_device import FakeDevice
from tests.test_web_events import NIGHT, record
from tests.ws_helpers import paired_token


def _event(trail: str | None, label: str | None = None) -> EventRecord:
    row = record("d")
    row.data = {**row.data, **({"trail": trail} if trail is not None else {})}
    row.label = label
    return row


def test_which_events_count() -> None:
    assert not events.dismissed(_event(None))
    assert not events.dismissed(_event("meteor"))
    assert events.dismissed(_event("moving"))
    assert events.dismissed(_event("star", label=None))
    assert not events.dismissed(_event("dashed", label="confirmed"))  # the owner's word
    assert events.dismissed(_event(None, label="false_positive"))
    assert events.trail(_event(None)) == "meteor"


async def test_queries_leave_them_out(app: FastAPI, client: httpx.AsyncClient) -> None:
    dev = FakeDevice()
    await paired_token(client, dev)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    start = dt.datetime(2026, 10, 8, 21, 0, tzinfo=dt.UTC)
    async with maker() as db:
        for i, (trail, label) in enumerate(
            [(None, None), ("meteor", None), ("moving", None), ("glare", "confirmed")]
        ):
            row = record(dev.device_id)
            row.event_id = f"meteor-20261008T2100{i:02d}Z"
            row.start = row.end = start + dt.timedelta(seconds=i)
            row.data = {**row.data, **({"trail": trail} if trail else {})}
            row.label = label
            db.add(row)
        await db.commit()
        shown = await events.events(db, dev.device_id, night_id=NIGHT, hide_false=True)
        assert sorted(e.event_id[-3:] for e in shown) == ["00Z", "01Z", "03Z"]
        await stats.refresh_night(db, dev.device_id, NIGHT)
        await db.commit()
        stat = await db.get(NightStat, (dev.device_id, NIGHT))
    assert stat is not None
    assert stat.meteors == 3  # the moving object is no meteor; the confirmed glare is
