"""What a station shows publicly (SPEC §10.6 Visibility): the owner's four choices on the web
and in the app, its public page, how results name it, and the week of notice for stations
that joined before the choices existed."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices import public
from allskyhub_server.mail.send import Mail, MemoryMailer
from allskyhub_server.models import Device, StationSource
from allskyhub_server.push.notify import Notifier
from tests.helpers import PASSWORD, csrf_from, make_account

PLACE = {"settings": {"latitude": 49.8912, "longitude": 10.8873, "timezone": "Europe/Berlin"}}


async def _new(client: httpx.AsyncClient, software: str, **fields: str) -> str:
    page = await client.get("/stationen/neu", params={"software": software})
    data = {"csrf_token": csrf_from(page.text), "software": software, "terms": "true", **fields}
    r = await client.post("/stationen/neu", data=data, follow_redirects=True)
    assert r.status_code == 200
    return r.url.path.rsplit("/", 1)[1]


async def _device(app: FastAPI, device_id: str) -> Device:
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        device = await db.get(Device, device_id)
        assert device is not None
        return device


async def _place(app: FastAPI, device_id: str) -> None:
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        device = await db.get(Device, device_id)
        assert device is not None
        device.last_status = PLACE
        await db.commit()


async def test_defaults_and_public_page(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    await make_account(client)
    form = await client.get("/stationen/neu", params={"software": "indi-allsky"})
    assert "Was öffentlich zu sehen ist" in form.text
    station_id = await _new(client, "indi-allsky", name="Sternwarte Musterberg")
    await _place(app, station_id)
    device = await _device(app, station_id)
    # Benjamin, 2026-11-07: named, on the map and a page, but no images.
    assert public.visibility(device) == public.Visibility()
    assert device.public_slug
    sections = public.profile(device).sections
    assert (sections.live, sections.loop, sections.events, sections.sky) == (
        False,
        False,
        False,
        True,
    )

    # Anyone sees the page, as a station and without pictures.
    page = await new_client.get(f"/sky/{device.public_slug}")
    assert page.status_code == 200
    assert "SharedSky-Station · indi-allsky" in page.text
    assert "Sternwarte Musterberg" in page.text
    assert "49.9° N, 10.9° O" in page.text  # rounded to 0.1°
    assert "Heute Nacht" in page.text
    assert "SharedSky-Netz" in page.text
    assert (await new_client.get(f"/sky/{device.public_slug}/latest.jpg")).status_code == 404
    # On the camera map, without an image.
    cams = (await new_client.get("/api/v1/public/cameras")).json()
    assert [c["title"] for c in cams] == ["Sternwarte Musterberg"]

    # The owner's page has the four choices and the link.
    own = await client.get(f"/stationen/{station_id}")
    assert 'id="oeffentlich"' in own.text
    assert f"/sky/{device.public_slug}" in own.text


async def test_choices_on_the_station_page(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    await make_account(client)
    # Connecting without a page and without the name.
    station_id = await _new(client, "other", name="Eigenbau", visibility="1", on_map="true")
    device = await _device(app, station_id)
    assert public.visibility(device) == public.Visibility(
        named=False, on_map=True, page=False, images=False
    )
    assert device.public_slug is None

    page = await client.get(f"/stationen/{station_id}")
    saved = await client.post(
        f"/stationen/{station_id}/oeffentlich",
        data={"csrf_token": csrf_from(page.text), "visibility": "1", "page": "true",
              "images": "true"},
        follow_redirects=True,
    )  # fmt: skip
    assert "Gespeichert." in saved.text
    device = await _device(app, station_id)
    assert public.visibility(device) == public.Visibility(
        named=False, on_map=False, page=True, images=True
    )
    first = device.public_slug
    assert first
    shown = await new_client.get(f"/sky/{first}")
    assert "Eine Station im Netz" in shown.text  # not named
    assert "Eigenbau" not in shown.text

    # Off and on again: a new address, the old one ends.
    for page_on in ("", "true"):
        await client.post(
            f"/stationen/{station_id}/oeffentlich",
            data={"csrf_token": csrf_from(page.text), "visibility": "1", "page": page_on},
        )
    device = await _device(app, station_id)
    assert device.public_slug not in (None, first)
    assert (await new_client.get(f"/sky/{first}")).status_code == 404

    # Only the owner chooses.
    await make_account(new_client)
    other = await new_client.get("/")
    refused = await new_client.post(
        f"/stationen/{station_id}/oeffentlich",
        data={"csrf_token": csrf_from(other.text), "visibility": "1"},
    )
    assert refused.status_code == 404


async def test_image_address_public_once_checked(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    await make_account(client)
    station_id = await _new(
        client, "image-url", name="Allsky Nordheim", url="https://cam.example.org/image.jpg",
        latitude="48.30", longitude="4.29", timezone="Europe/Vienna", proof_text="Meine.",
    )  # fmt: skip
    device = await _device(app, station_id)
    assert device.public_slug
    # Someone's image address is no one's page until an admin checked it (SPEC §10.7).
    assert (await new_client.get(f"/sky/{device.public_slug}")).status_code == 404
    assert (await new_client.get("/api/v1/public/cameras")).json() == []
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    for state, by, code in (("approved", "admin@example.org", 200), ("paused", "pause", 404),
                            ("paused", "admin@example.org", 200)):  # fmt: skip
        async with maker() as db:
            source = await db.get(StationSource, station_id)
            assert source is not None
            source.state, source.reviewed_by = state, by
            await db.commit()
        page = await new_client.get(f"/sky/{device.public_slug}")
        assert page.status_code == code, (state, by)
    assert "SharedSky-Station · Allsky" in page.text


async def test_how_results_name_a_station() -> None:
    owner = None
    station = Device(id="S" * 26, name="Hochrhön", kind="station", named=True)
    camera = Device(id="C" * 26, name="Garten", kind="camera")
    assert public.credit(station, None) == {"name": "Hochrhön", "link": None}
    assert public.credit(camera, None) == {"name": "eine Kamera im Kameranetz", "link": None}
    station.named = False
    assert public.credit(station, None) == {"name": "eine Station im Netz", "link": None}
    station.named, station.public_slug = False, "abc"
    assert public.credit(station, owner) == {"name": "eine Station im Netz", "link": "/sky/abc"}
    # During its week of notice a station that joined before is not named yet.
    station.named, station.public_slug = True, None
    station.public_from = dt.datetime.now(dt.UTC) + dt.timedelta(days=3)
    assert public.credit(station, None) == {"name": "eine Station im Netz", "link": None}


class _FailingMailer(MemoryMailer):
    async def send(self, mail: Mail) -> bool:
        return False


async def test_week_of_notice_for_stations_that_joined_before(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    await make_account(client)
    keep = await _new(client, "other", name="Altstation")
    chooses = await _new(client, "other", name="Wählerisch")
    now = dt.datetime.now(dt.UTC)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        for station_id in (keep, chooses):
            device = await db.get(Device, station_id)
            assert device is not None
            # As the migration leaves a station that joined before (0050).
            device.public_slug = None
            device.public_from = public.UNMAILED
        await db.commit()
    notifier = Notifier(maker, None)
    notifier.base_url = "https://hub.example.org"
    later = now + public.NOTICE * 2

    # No mail set up, or sending fails: nothing goes public, however long it waits.
    await notifier.check_station_notice(later)
    notifier.mailer = _FailingMailer()
    await notifier.check_station_notice(later)
    for station_id in (keep, chooses):
        device = await _device(app, station_id)
        assert (device.public_slug, device.public_from, device.public_mailed_at) == (
            None,
            public.UNMAILED,
            None,
        )
    page = await client.get(f"/stationen/{keep}")
    assert "eine Woche nach unserer Mail an dich" in page.text

    # The mail goes out (and is tried again after the failure): a week from then.
    mailer = MemoryMailer()
    notifier.mailer = mailer
    await notifier.check_station_notice(later)
    await notifier.check_station_notice(later)  # once
    assert sorted(m.subject.split(" wird")[0] for m in mailer.sent) == [
        "SharedSky: Altstation",
        "SharedSky: Wählerisch",
    ]
    day = (later + public.NOTICE).astimezone(ZoneInfo("Europe/Berlin")).strftime("%d.%m.%Y")
    assert all(day in m.subject for m in mailer.sent)
    assert f"https://hub.example.org/stationen/{keep}#oeffentlich" in "".join(
        m.body for m in mailer.sent
    )
    kept = await _device(app, keep)
    assert (kept.public_slug, kept.public_from) == (None, later + public.NOTICE)

    # The owner's page tells the date; saving a choice ends the notice.
    page = await client.get(f"/stationen/{chooses}")
    assert "Deine Station war schon dabei" in page.text
    await client.post(
        f"/stationen/{chooses}/oeffentlich",
        data={"csrf_token": csrf_from(page.text), "visibility": "1", "named": "true"},
    )

    await notifier.check_station_notice(later + public.NOTICE - dt.timedelta(minutes=1))
    assert (await _device(app, keep)).public_slug is None  # not before the week is over
    await notifier.check_station_notice(later + public.NOTICE)
    kept = await _device(app, keep)
    assert (kept.public_slug is not None, kept.public_from) == (True, None)
    chose = await _device(app, chooses)
    assert (chose.public_slug, chose.public_from) == (None, None)
    assert len(mailer.sent) == 2


async def test_app_choices(client: httpx.AsyncClient, new_client: httpx.AsyncClient) -> None:
    email = await make_account(client)
    login = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    auth = {"Authorization": f"Bearer {login.json()['access_token']}"}
    # The app 1.2.0 does not ask: the defaults.
    old = await client.post(
        "/api/v1/stations", headers=auth,
        data={"software": "other", "name": "Eigenbau", "terms": "true"},
    )  # fmt: skip
    station = old.json()["station"]
    assert station["visibility"] == {"named": True, "on_map": True, "page": True, "images": False}
    assert station["public_url"].startswith("http://testserver/sky/")
    asked = await client.post(
        "/api/v1/stations", headers=auth,
        data={"software": "other", "name": "Zweite", "terms": "true", "page": "false"},
    )  # fmt: skip
    assert asked.json()["station"]["public_url"] is None

    r = await client.put(
        f"/api/v1/stations/{station['id']}/visibility", headers=auth,
        json={"named": False, "on_map": False, "page": True, "images": True},
    )  # fmt: skip
    assert r.status_code == 200
    assert r.json()["visibility"] == {
        "named": False,
        "on_map": False,
        "page": True,
        "images": True,
    }
    assert r.json()["public_url"] == station["public_url"]  # the page keeps its address
    got = await client.get(f"/api/v1/stations/{station['id']}", headers=auth)
    assert got.json()["visibility"]["named"] is False

    other = await make_account(new_client)
    login2 = await new_client.post(
        "/api/v1/auth/login", json={"email": other, "password": PASSWORD}
    )
    auth2 = {"Authorization": f"Bearer {login2.json()['access_token']}"}
    refused = await new_client.put(
        f"/api/v1/stations/{station['id']}/visibility", headers=auth2, json={}
    )
    assert refused.status_code == 404
