"""The network in numbers (devices/network.py, SPEC §10.6): "Im Netz", "Himmel im
Vergleich" and the page /netz."""

from __future__ import annotations

import datetime as dt

import httpx
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices import network, public, stations
from allskyhub_server.devices.stations import SecretKey
from allskyhub_server.models import Device, MeteorPair, NightStat, StationSource, User
from tests.helpers import PASSWORD, make_account

NOW = dt.datetime.now(dt.UTC)


async def _stations(
    app: FastAPI, client: httpx.AsyncClient, email: str | None = None
) -> dict[str, str]:
    """Four stations near Bamberg, all with the defaults, and one more 400 km away."""
    email = await make_account(client, email)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    places = {
        "Sternwarte Musterberg": (49.89, 10.89),
        "Hochrhön": (50.45, 9.95),
        "Hügelfeld": (50.25, 10.96),
        "Ohne Namen": (49.6, 1.0),
        "Weit weg": (53.55, 10.0),
    }
    ids: dict[str, str] = {}
    async with maker() as db:
        user = await db.scalar(select(User).where(User.email == email))
        assert user is not None
        for name, (lat, lon) in places.items():
            device, _ = await stations.create(
                db, user, name, "other", SecretKey(app.state.stores.vault)
            )
            public.set_visibility(device, public.Visibility(named=name != "Ohne Namen"))
            device.terms_accepted_at = NOW - dt.timedelta(days=30)
            device.last_status = {"settings": {"latitude": lat, "longitude": lon}}
            device.last_seen_at = NOW
            ids[name] = device.id
        await db.commit()
    return ids


async def _nights(app: FastAPI, ids: dict[str, str]) -> None:
    """Ten clear nights; Musterberg 0.4 darker than its neighbours."""
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    last = dt.date.fromisoformat(stations.night_id(NOW, 10.9))
    base = {"Sternwarte Musterberg": 21.4, "Hochrhön": 21.1, "Hügelfeld": 20.9,
            "Ohne Namen": 21.0, "Weit weg": 19.5}  # fmt: skip
    async with maker() as db:
        for name, device_id in ids.items():
            for k in range(10):
                night = (last - dt.timedelta(days=k)).strftime("%Y%m%d")
                db.add(NightStat(device_id=device_id, night_id=night, clear_hours=None,
                                 best_sqm=base[name], meteors=3,
                                 showers={"Leoniden": 2, "sporadisch": 1}))  # fmt: skip
        await db.commit()


async def test_sky_compared_with_the_neighbours(app: FastAPI, client: httpx.AsyncClient) -> None:
    ids = await _stations(app, client)
    await _nights(app, ids)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        device = await db.get(Device, ids["Sternwarte Musterberg"])
        assert device is not None
        near = {d.id for d in await network.neighbours(db, device)}
        assert ids["Weit weg"] not in near  # 400 km
        found = await network.comparison(db, device, NOW)
    assert found is not None
    assert found.neighbours == 3
    assert found.difference == 0.4  # 21.4 against the median 21.0
    clear = [n for n in found.nights if n.own is not None]
    assert len(clear) == 10
    assert (clear[-1].low, clear[-1].high) == (20.9, 21.1)
    chart = found.chart()
    assert chart["path"].startswith("M")
    assert chart["last"] is not None
    assert len(chart["bars"]) == 10
    assert [t["label"] for t in chart["ticks"]] == ["20.5", "21.0", "21.5"]

    # Too few neighbours: no comparison.
    async with maker() as db:
        far = await db.get(Device, ids["Weit weg"])
        assert far is not None
        assert await network.comparison(db, far, NOW) is None


async def test_part_and_network_page(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    ids = await _stations(app, client)
    await _nights(app, ids)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    a, b, c = ids["Sternwarte Musterberg"], ids["Hügelfeld"], ids["Ohne Namen"]
    async with maker() as db:
        pair = MeteorPair(
            device_a=min(a, b), event_a="meteor-1", device_b=max(a, b), event_b="meteor-2",
            at=NOW - dt.timedelta(hours=2), high_lat=50.0, high_lon=10.5, high_km=92.0,
            low_lat=50.1, low_lon=10.7, low_km=41.0, length_km=60.0, convergence_deg=30.0,
            peak=0.95, cameras=[a, b, c],
        )  # fmt: skip
        db.add(pair)
        await db.commit()
        device = await db.get(Device, a)
        assert device is not None
        part = await network.part(db, device)
    assert (part.fireballs, part.meteors) == (1, 30)
    assert [o["name"] for o in part.recent[0].others] == ["Hügelfeld", "eine Station im Netz"]

    # The station's public page: "Im Netz" and the comparison.
    async with maker() as db:
        slug = (await db.get(Device, a)).public_slug  # type: ignore[union-attr]
    page = await new_client.get(f"/sky/{slug}")
    assert "Im Netz" in page.text
    assert "Himmel im Vergleich" in page.text
    assert "0,4 mag/⁠″² dunkler als die Nachbarn" in page.text

    # A station in its week of notice and an unchecked image address stay off /netz.
    async with maker() as db:
        late = await db.get(Device, ids["Weit weg"])
        assert late is not None
        late.public_from = NOW + dt.timedelta(days=3)
        late.public_slug = None
        user = await db.get(User, late.owner_id)
        assert user is not None
        unchecked, _ = await stations.create(db, user, "Fremde Adresse", "image-url",
                                             SecretKey(app.state.stores.vault))  # fmt: skip
        public.set_visibility(unchecked, public.Visibility())
        db.add(StationSource(device_id=unchecked.id, url="https://x.example.org/i.jpg"))
        await db.commit()

    net = await new_client.get("/netz")
    assert net.status_code == 200
    text = net.text
    assert "Gemeinsam den Himmel vermessen" in text
    assert "4 Kameras messen mit: 0 allskyhub-Kameras und 4 SharedSky-Stationen" in text
    for name in ("Sternwarte Musterberg", "Hochrhön", "Hügelfeld", "eine Station im Netz"):
        assert name in text
    assert "Ohne Namen" not in text
    assert "Weit weg" not in text
    assert "Fremde Adresse" not in text
    assert f"/feuerkugeln/{pair.id}" in text
    assert "Leoniden" in text
    assert "21,40" in text  # the darkest sky last night
    assert 'href="/netz"' in text  # in the menu
    async with maker() as db:
        view = await network.overview(db, NOW)
    assert (view.stations, view.active, view.fireballs_month) == (4, 4, 1)
    assert sorted(p.title for p in view.points) == [
        "Hochrhön", "Hügelfeld", "Sternwarte Musterberg", "eine Station im Netz",
    ]  # fmt: skip


async def test_network_for_the_app(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    ids = await _stations(app, client, "owner-net@example.org")
    await _nights(app, ids)
    r = await new_client.get("/api/v1/public/network")
    assert r.status_code == 200
    body = r.json()
    assert (body["stations"], body["cameras"]) == (5, 0)
    assert body["darkest"][0]["name"] == "Sternwarte Musterberg"
    assert body["darkest"][0]["slug"]
    assert {"name": "Leoniden", "count": 10} in body["showers_tonight"]
    assert "eine Station im Netz" in [m["name"] for m in body["members"]]
    assert "Ohne Namen" not in r.text

    # The owner's own station: its part and the comparison; no one else's.
    login = await client.post(
        "/api/v1/auth/login", json={"email": "owner-net@example.org", "password": PASSWORD}
    )
    auth = {"Authorization": f"Bearer {login.json()['access_token']}"}
    mine = await client.get(f"/api/v1/devices/{ids['Sternwarte Musterberg']}/network", headers=auth)
    assert mine.status_code == 200
    part = mine.json()
    assert part["meteors"] == 30
    assert part["comparison"]["neighbours"] == 3
    assert part["comparison"]["difference"] == 0.4
    assert len(part["comparison"]["nights"]) == 30
    other = await make_account(new_client)
    login2 = await new_client.post(
        "/api/v1/auth/login", json={"email": other, "password": PASSWORD}
    )
    auth2 = {"Authorization": f"Bearer {login2.json()['access_token']}"}
    refused = await new_client.get(
        f"/api/v1/devices/{ids['Sternwarte Musterberg']}/network", headers=auth2
    )
    assert refused.status_code == 404


async def test_overview_is_computed_once_a_minute(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    await _stations(app, client)
    cache: network.OverviewCache = app.state.network
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        first = await cache.get(db, NOW)
        assert await cache.get(db, NOW + dt.timedelta(seconds=59)) is first
        assert await cache.get(db, NOW + dt.timedelta(seconds=60)) is not first
    assert (await new_client.get("/netz")).status_code == 200
