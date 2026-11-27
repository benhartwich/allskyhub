"""The public pages beyond the sky pages in English too (2026-11-27: a visitor from New
Zealand saw pages half English, half German): the camera map, Plus, the parts list,
fireballs, meteor showers and the imprint, with ?lang=en (web/lang.py)."""

from __future__ import annotations

import datetime as dt
import json

import httpx
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices import public, stations
from allskyhub_server.devices.stations import SecretKey
from allskyhub_server.models import MeteorPair, NightStat, User
from tests.helpers import make_account
from tests.test_fireball import BOTTOM, NORDHEIM, SUEDTAL, TOP
from tests.test_public_page import _add_archive, _public, _save  # pyright: ignore[reportPrivateUsage]


def _english(page: httpx.Response, present: tuple[str, ...], absent: tuple[str, ...]) -> str:
    assert page.status_code == 200
    text = page.text
    assert '<html lang="en">' in text
    for sentence in present:
        assert sentence in text, sentence
    body = text.split("<main>")[1].split('<footer class="app-foot">')[0]
    for word in absent:
        assert word not in body, word
    return text


async def test_camera_map_in_english(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    device_id, _ = await _public(app, client, new_client)
    await _add_archive(app, device_id)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    today = dt.datetime.now(dt.UTC).date()
    async with maker() as db:
        for i, sqm in enumerate((21.4, 21.5, 21.6)):
            night = (today - dt.timedelta(days=i + 1)).strftime("%Y%m%d")
            db.add(NightStat(device_id=device_id, night_id=night, best_sqm=sqm, clear_hours=5.0))
        await db.commit()
    await _save(client, device_id, title="Sternwarte", show_coordinates="on", section_sky="on")
    page = _english(
        await new_client.get("/karte", params={"layer": "sqm", "lang": "en"}),
        ("Camera map", "Sky brightness", "light pollution", "very dark (≥ 21.7)",
         "no value yet", "Search a place", "Night lights"),
        ("Kamerakarte", "Himmelshelligkeit", "Suchen", "Nachtlichter", "dunkel", ">Satellit<"),
    )  # fmt: skip
    config = json.loads(page.split('id="map-config">')[1].split("</script>")[0])
    assert config["cameras"][0]["sqm_text"] == "21.50\u00a0mag/\u2060″² · dark"
    german = (await new_client.get("/karte", params={"layer": "sqm", "lang": "de"})).text
    assert '<html lang="de">' in german
    assert "21,50\u00a0mag/\u2060″² · dunkel" in german
    assert "sehr dunkel (≥ 21,7)" in german


async def test_plus_hardware_imprint_in_english(client: httpx.AsyncClient) -> None:
    _english(
        await client.get("/plus", params={"lang": "en"}),
        ("A long memory for your camera", "€4.99 a month", "€49 a year", "How it works"),
        ("Kostenlos", "Vollbilder", "Monat", "Zahlung", "4,99"),
    )
    _english(
        await client.get("/hardware", params={"lang": "en"}),
        ("What you need for a camera", "≈ €500", "Stray-light shield", "Pole mount",
         "Silica gel bags", "From an order of €240", "Ask for access"),
        ("Gehäuse", "Rechner und Kamera", "Befestigung", ">Streulichtschutz<", "Löten",
         "Zugang", "0 €"),
    )  # fmt: skip
    _english(
        await client.get("/impressum", params={"lang": "en"}),
        ("Imprint", "Operator", "Phone", "Liability for content and links", "Austria"),
        ("Betreiber", "Telefon", "Haftung", "Quelltext", "Verleihungsstaat"),
    )
    german = (await client.get("/hardware", params={"lang": "de"})).text
    assert '<html lang="de">' in german
    assert "Was du für eine Kamera brauchst" in german
    assert "189–194 €" in german


async def _network_station(app: FastAPI, client: httpx.AsyncClient, name: str) -> str:
    email = await make_account(client, f"{name.lower()}@example.org")
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        user = await db.scalar(select(User).where(User.email == email))
        assert user is not None
        device, _ = await stations.create(
            db, user, name, "other", SecretKey(app.state.stores.vault)
        )
        public.set_visibility(device, public.Visibility(named=True))
        device.network = True
        device.terms_accepted_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=30)
        lat, lon = NORDHEIM if name == "Nordheim" else SUEDTAL
        device.last_status = {"settings": {"latitude": lat, "longitude": lon}}
        await db.commit()
        return device.id


async def test_fireballs_and_showers_in_english(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    a = await _network_station(app, client, "Nordheim")
    b = await _network_station(app, client, "Suedtal")
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        pair = MeteorPair(
            device_a=min(a, b), event_a="meteor-1", device_b=max(a, b), event_b="meteor-2",
            at=dt.datetime.now(dt.UTC) - dt.timedelta(hours=2), high_lat=TOP[0],
            high_lon=TOP[1], high_km=TOP[2], low_lat=BOTTOM[0], low_lon=BOTTOM[1],
            low_km=BOTTOM[2], length_km=60.0, convergence_deg=30.0, peak=0.95, cameras=[a, b],
        )  # fmt: skip
        db.add(pair)
        await db.commit()
        pair_id = pair.id
    _english(
        await new_client.get("/feuerkugeln", params={"lang": "en"}),
        ("Fireballs", "Bright meteors that two cameras", "km high, heading southeast",
         "Meteorite fall possible"),
        ("Feuerkugel", "Höhe", "flog nach", "Uhr", "Meteoritenfall"),
    )  # fmt: skip
    _english(
        await new_client.get(f"/feuerkugeln/{pair_id}"),  # the cookie keeps English
        ("All fireballs", "Fireball on ", "Length of the path", "heading southeast", "Seen by",
         "Possible fall area", "Report to the IMO"),
        ("Feuerkugel", "Höhe", "Fallgebiet", "Gesehen", " und ", "Uhr", "Südosten"),
    )  # fmt: skip
    _english(
        await new_client.get("/meteorstroeme"),
        ("Meteor showers live", "The last 24 hours", "meteors per hour"),
        ("Meteorströme", "Meteore", "Stunde", "Kameras", "Radianten", "heute"),
    )
    german = (await new_client.get(f"/feuerkugeln/{pair_id}", params={"lang": "de"})).text
    assert '<html lang="de">' in german
    assert "Mögliches Fallgebiet" in german
    assert "flog nach Südosten" in german
