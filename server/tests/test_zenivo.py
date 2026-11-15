"""Zenivo's sync as it is (SharedSky, SPEC §10.9, api/zenivo.py)."""

from __future__ import annotations

import dataclasses
import datetime as dt
import io
import re

import httpx
from fastapi import FastAPI
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.api import zenivo
from allskyhub_server.devices import station_flow, stations
from allskyhub_server.devices.stations import SecretKey
from allskyhub_server.models import Device, User
from tests.helpers import PASSWORD, csrf_from, make_account

W, H = 1600, 1200
LAT, LON = 50.04, 10.39  # the usual made-up test place


def _jpeg(width: int = W, height: int = H) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), (12, 14, 20)).save(out, "JPEG", quality=90)
    return out.getvalue()


async def _station(
    app: FastAPI, client: httpx.AsyncClient, software: str = "zenivo"
) -> tuple[str, str]:
    email = await make_account(client)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        user = await db.scalar(select(User).where(User.email == email))
        assert user is not None
        device, secret = await stations.create(
            db, user, "Gartensternwarte", software, SecretKey(app.state.stores.vault)
        )
        device.last_status = {
            "settings": {"latitude": LAT, "longitude": LON, "elevation_m": 300.0},
            "station": {"software": software, "lens": None},
        }
        await db.commit()
        return device.id, secret


def _base(station_id: str, secret: str) -> str:
    return f"/zenivo/{station_id}.{secret}/api/allsky/ingest"


async def _send_frame(
    client: httpx.AsyncClient, base: str, exposure: str = "20.00"
) -> httpx.Response:
    """As Zenivo's SyncClient.send_frame does it."""
    return await client.post(
        f"{base}/frame",
        files={"image": ("frame.jpg", _jpeg(), "image/jpeg")},
        headers={"X-Device-Id": "zenivo-gartenpi", "X-Exposure-Sec": exposure,
                 "X-Gain-Db": "12.0"},
    )  # fmt: skip


async def _device(app: FastAPI, station_id: str) -> Device:
    async with app.state.sessionmaker() as db:
        device = await db.get(Device, station_id)
        assert device is not None
        return device


async def test_frame_takes_the_time_of_its_telemetry(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    station_id, secret = await _station(app, client)
    base = _base(station_id, secret)
    r = await _send_frame(client, base)
    assert (r.status_code, r.json()) == (200, {"ok": True})
    end = dt.datetime.now(dt.UTC).replace(microsecond=0) - dt.timedelta(seconds=2)
    local = end.astimezone(dt.timezone(dt.timedelta(hours=1)))  # Zenivo sends local time
    r = await client.post(
        f"{base}/telemetry",
        json={"capture_time": local.isoformat(), "exposure_s": 20.0, "gain_db": 12.0,
              "star_count": 412, "cloud_cover_pct": 3.0, "histogram": [0] * 256},
    )  # fmt: skip
    assert r.status_code == 200
    device = await _device(app, station_id)
    frame = device.last_frame or {}
    assert dt.datetime.fromisoformat(frame["time"]) == end - dt.timedelta(seconds=10)
    assert frame["time_source"] == "station"
    assert frame["clock_synced"] is True
    assert frame["stars"] == 412
    info = (device.last_status or {})["station"]
    assert (info["width"], info["height"]) == (W, H)
    assert device.last_seen_at is not None


async def test_frame_without_telemetry_is_never_trusted(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    station_id, secret = await _station(app, client)
    base = _base(station_id, secret)
    before = dt.datetime.now(dt.UTC)
    assert (await _send_frame(client, base, exposure="4.00")).status_code == 200
    # The next frame comes without telemetry in between: the first one takes its arrival.
    assert (await _send_frame(client, base)).status_code == 200
    device = await _device(app, station_id)
    frame = device.last_frame or {}
    taken = dt.datetime.fromisoformat(frame["time"])
    assert before - dt.timedelta(seconds=3) <= taken <= dt.datetime.now(dt.UTC)
    assert frame["time_source"] == "fetch"
    assert (device.clock or {}).get("trusted") is False


async def test_logins_and_other_paths(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    station_id, secret = await _station(app, client)
    other_id, other_secret = await _station(app, new_client, "other")
    assert (await _send_frame(client, _base(station_id, "f" * 64))).status_code == 401
    assert (await _send_frame(client, f"/zenivo/{station_id}/api/allsky/ingest")).status_code == 401
    # A connect/v1 station's secret does not open the Zenivo door.
    assert (await _send_frame(client, _base(other_id, other_secret))).status_code == 401
    base = _base(station_id, secret)
    for kind in ("event", "summary"):
        r = await client.post(f"{base}/{kind}", files={"file": ("k.jpg", b"x", "image/jpeg")},
                              data={"type": "keogram", "date": "20261115"})  # fmt: skip
        assert (r.status_code, r.json()) == (200, {"ok": True})
    assert (await client.post(f"{base}/anything")).status_code == 404
    # Telemetry without a frame before it is fine, and broken parts are refused.
    assert (await client.post(f"{base}/telemetry", json={"star_count": 1})).status_code == 200
    assert (await client.post(f"{base}/telemetry", content=b"[1, 2")).status_code == 400
    bad = await client.post(f"{base}/frame", files={"image": ("f.jpg", b"no jpeg", "image/jpeg")})
    assert (bad.status_code, bad.json()["error"]) == (400, "bad_image")


async def test_connect_a_zenivo_camera(app: FastAPI, client: httpx.AsyncClient) -> None:
    await make_account(client)
    page = await client.get("/stationen/neu", params={"software": "zenivo"})
    assert page.status_code == 200
    assert 'name="latitude"' in page.text
    assert 'name="proof_text"' not in page.text  # no admin check (§10.9)
    data = {"csrf_token": csrf_from(page.text), "software": "zenivo", "terms": "true",
            "name": "Gartensternwarte", "timezone": "Europe/Berlin"}  # fmt: skip
    missing = await client.post("/stationen/neu", data=data)
    assert missing.status_code == 400
    r = await client.post(
        "/stationen/neu",
        data={**data, "latitude": str(LAT), "longitude": str(LON), "elevation_m": "300"},
        follow_redirects=True,
    )
    assert r.status_code == 200
    station_id = r.url.path.rsplit("/", 1)[1]
    found = re.search(
        rf"--sync-target http://testserver/zenivo/{station_id}\.([0-9a-f]{{64}})", r.text
    )
    assert found
    device = await _device(app, station_id)
    assert device.software == "zenivo"
    assert (device.last_status or {})["settings"]["latitude"] == LAT
    # The address works.
    assert (await _send_frame(client, _base(station_id, found[1]))).status_code == 200


async def test_late_telemetry_and_bad_values(app: FastAPI, client: httpx.AsyncClient) -> None:
    """Telemetry later than 30 s, an absurd exposure and a year-1 time do not break
    anything (allsky review of #460)."""
    station_id, secret = await _station(app, client)
    base = _base(station_id, secret)
    assert (await _send_frame(client, base, exposure="1e14")).status_code == 200
    pending: zenivo.Pending = app.state.zenivo
    kept = pending._frames[station_id]  # pyright: ignore[reportPrivateUsage]
    assert kept.exposure_s == zenivo.EXPOSURE_MAX_S
    assert kept.path.is_file()  # waits on disk, not in memory
    pending._frames[station_id] = dataclasses.replace(  # pyright: ignore[reportPrivateUsage]
        kept, arrived=kept.arrived - dt.timedelta(seconds=31), exposure_s=4.0
    )
    r = await client.post(f"{base}/telemetry", json={"capture_time": "0001-01-01T00:00:00+00:00"})
    assert r.status_code == 200
    device = await _device(app, station_id)
    assert (device.last_frame or {})["time_source"] == "fetch"  # too late for its telemetry
    assert not kept.path.exists()
    # A year-1 capture_time in time is no time either.
    assert (await _send_frame(client, base)).status_code == 200
    r = await client.post(f"{base}/telemetry", json={"capture_time": "0001-01-01T00:00:00+00:00"})
    assert r.status_code == 200
    assert ((await _device(app, station_id)).last_frame or {})["time_source"] == "fetch"
    assert (await client.get(f"{base}/frame")).status_code == 404


async def test_size_limits(app: FastAPI, client: httpx.AsyncClient) -> None:
    station_id, secret = await _station(app, client)
    base = _base(station_id, secret)
    big = await client.post(
        f"{base}/frame", files={"image": ("f.jpg", b"\xff" * (8 * 1024 * 1024 + 1), "image/jpeg")}
    )
    assert big.status_code == 413
    long = await client.post(f"{base}/telemetry", content=b"[" + b"1," * 200_000 + b"1]")
    assert long.status_code == 413


async def test_removing_the_station_drops_its_waiting_frame(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    station_id, secret = await _station(app, client)
    assert (await _send_frame(client, _base(station_id, secret))).status_code == 200
    pending: zenivo.Pending = app.state.zenivo
    path = pending._frames[station_id].path  # pyright: ignore[reportPrivateUsage]
    async with app.state.sessionmaker() as db:
        device = await db.get(Device, station_id)
        assert device
        await station_flow.remove(db, device, app.state, app.state.settings)
    assert station_id not in pending._frames  # pyright: ignore[reportPrivateUsage]
    assert not path.exists()


async def test_app_gets_the_sync_target(client: httpx.AsyncClient) -> None:
    email = await make_account(client)
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
    made = await client.post(
        "/api/v1/stations", headers=auth,
        data={"software": "zenivo", "name": "Garten", "terms": "true",
              "latitude": str(LAT), "longitude": str(LON)},
    )  # fmt: skip
    assert made.status_code == 200
    body = made.json()
    sid = body["station"]["id"]
    assert body["base_url"] == f"http://testserver/zenivo/{sid}.{body['secret']}"
    renewed = await client.post(
        f"/api/v1/stations/{sid}/secret", headers=auth, json={"password": PASSWORD}
    )
    new = renewed.json()
    assert new["base_url"] == f"http://testserver/zenivo/{sid}.{new['secret']}"
