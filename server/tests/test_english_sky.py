# pyright: reportPrivateUsage=false
"""The public sky pages in English (a visitor from New Zealand, 2026-11-27: pages half
English, half German): /sky/<slug>, its night, its measurements, a station's page and the
live fragment with ``?lang=en`` or the cookie (web/lang.py), German unchanged otherwise."""

from __future__ import annotations

import dataclasses
import datetime as dt
import re

import httpx
from fastapi import FastAPI
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices import sky_layers, sky_overlay
from allskyhub_server.devices.event_text import compass
from allskyhub_server.models import Device
from allskyhub_server.web.templating import age, filesize, night_title, stamp, when
from tests.test_network import _nights, _stations
from tests.test_observatory import _status as observatory
from tests.test_public_page import (
    _add_archive,
    _public,
    _save,
)
from tests.test_sky_now import SKY
from tests.test_sky_overlay import AT, _status


def _visible(page: str) -> str:
    """The page's text without scripts and tags (German may stay in code and URLs)."""
    page = re.sub(r"<script.*?</script>", " ", page, flags=re.S)
    return re.sub(r"<[^>]+>", " ", page)


def _english(page: httpx.Response, present: list[str], absent: list[str]) -> None:
    assert page.status_code == 200
    assert '<html lang="en">' in page.text
    text = _visible(page.text)
    for sentence in present:
        assert sentence in page.text, sentence
    for german in absent:  # as words: "Archiv" is in "Archive"
        assert not re.search(rf"(?<!\w){re.escape(german)}(?!\w)", text), german
    assert not re.search(r"\d Uhr\b", page.text)


async def _camera(app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient) -> str:
    """A public camera at 50.04/10.39 with a lens, the sky, a telescope and an archive."""
    device_id, slug = await _public(app, client, new_client)
    await _add_archive(app, device_id)
    status = {**observatory(True).model_dump(mode="json"), **_status()}
    status["sky"] = {**SKY, "at": dt.datetime.now(dt.UTC).isoformat()}
    status["settings"] = {"latitude": 50.04, "longitude": 10.39, "timezone": "Europe/Berlin"}
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        await db.execute(
            update(Device)
            .where(Device.id == device_id)
            .values(last_status=status, last_frame={"mode": "night", "sun_elevation": -30.2,
                                                    "exposure_us": 15_000_000, "gain": 200})
        )  # fmt: skip
        await db.commit()
    sections = {f"section_{s}": "on" for s in ("live", "loop", "archive", "events", "sky",
                                               "aurora", "observatory", "aircraft")}  # fmt: skip
    await _save(client, device_id, title="Sternwarte", location_label="Nordheim",
                camera="ZWO ASI678MC", lens="2,5 mm", computer="Pi 4", owner="Ben",
                show_coordinates="on", **sections)  # fmt: skip
    return slug


async def test_camera_page_in_english(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    slug = await _camera(app, client, new_client)
    page = await new_client.get(f"/sky/{slug}", params={"lang": "en"})
    _english(
        page,
        ["Sternwarte · Live sky", "All-sky camera · Nordheim", "50.0° N, 10.4° E",
         "About this camera", "Run by", "The last hour", "▶ Play", "Aurora outlook",
         "Rule of thumb for this place", "Recently in the sky", "Archive", "Keogram",
         "Telescope", "812 stars", "HFR 2.31", "Tonight", "Constellations", "Ursa Minor",
         "Moon &amp; planets", "well calibrated", "Sky brightness", "20.63", "Taken",
         "Mon 12 Oct → Tue 13 Oct", "3 images · 3 events", "Red light for the night"],
        ["Himmel live", "Allsky-Kamera", "Über diese Kamera", "Betrieben von",
         "Die letzte Stunde", "Abspielen", "Polarlicht", "Faustregel", "Zuletzt am Himmel",
         "Archiv", "Keogramm", "Teleskop", "Sterne", "Heute Nacht", "Sternbilder",
         "Kleiner Bär", "eingemessen", "Himmelshelligkeit", "Aufgenommen", "Bewölkung",
         "Mo 12.10.", "Bilder", "Ereignis", "Rotlicht", "beleuchtet", "Wolken", "Dunkel",
         "Ebenen", "Vollbild"],
    )  # fmt: skip
    # The cookie keeps the language for the live part the page polls.
    now = await new_client.get(f"/sky/{slug}/now")
    assert "Latest sky image" in now.text
    assert "Aufgenommen" not in now.text
    widget = await new_client.get(f"/sky/{slug}/widget")
    assert '<html lang="en">' in widget.text
    assert "Taken" in widget.text

    german = await new_client.get(f"/sky/{slug}", params={"lang": "de"})
    assert '<html lang="de">' in german.text
    for text in ("Über diese Kamera", "Die letzte Stunde", "Polarlicht-Aussichten",
                 "Mo 12.10. → Di 13.10.", "Kleiner Bär", "50,0° N, 10,4° O",
                 "gut eingemessen", "20,63"):  # fmt: skip
        assert text in german.text, text
    assert "Ursa Minor" not in german.text


async def test_night_and_measurements_in_english(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    slug = await _camera(app, client, new_client)
    night = await new_client.get(f"/sky/{slug}/nights/20261012", params={"lang": "en"})
    _english(
        night,
        ["Mon 12 Oct → Tue 13 Oct · Sternwarte", "Events", "1 meteor: 1 Orionids", "Image "],
        ["Ereignisse", "Aufnahme", "Orioniden", "Mo 12.10."],
    )
    charts = await new_client.get(f"/sky/{slug}/charts", params={"lang": "en"})
    _english(
        charts,
        ["Measurements · Sternwarte", "About the values", "Reset zoom", "Twilight",
         "higher values = darker", "Local time of the camera", "3 days"],
        ["Messdaten", "Zu den Werten", "Dämmerung", "Zoom zurücksetzen", "Ortszeit", "Tage"],
    )  # fmt: skip
    german = await new_client.get(f"/sky/{slug}/charts", params={"lang": "de"})
    assert "Zu den Werten" in german.text
    assert "Ortszeit der Kamera" in german.text


async def test_station_page_in_english(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    ids = await _stations(app, client)
    await _nights(app, ids)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        device = await db.get(Device, ids["Sternwarte Musterberg"])
        assert device is not None
        slug = device.public_slug
    page = await new_client.get(f"/sky/{slug}", params={"lang": "en"})
    _english(
        page,
        ["SharedSky-Station · own software", "waiting for a clear night", "still being checked",
         "In the SharedSky network since 20", "In the network", "Sky compared",
         "0.4 mag/⁠″² darker than the neighbours", "Its measurements are under CC BY 4.0"],
        ["eigene Software", "wartet auf", "wird noch geprüft", "Seit ", "Im Netz",
         "Himmel im Vergleich", "Nachbarn", "Ihre Messwerte"],
    )  # fmt: skip
    german = await new_client.get(f"/sky/{slug}", params={"lang": "de"})
    assert "SharedSky-Station · eigene Software" in german.text
    assert "im SharedSky-Netz." in german.text


def test_dates_and_directions_in_english() -> None:
    assert night_title("20261006") == "Di 06.10. → Mi 07.10."
    assert night_title("20261006", en=True) == "Tue 6 Oct → Wed 7 Oct"
    at = dt.datetime(2026, 10, 14, 20, 14, tzinfo=dt.UTC)
    assert when(at) == "14.10.2026, 22:14"
    assert when(at, en=True) == "2026-10-14, 22:14"
    old = dt.datetime.now(dt.UTC) - dt.timedelta(days=3, minutes=1)
    assert (age(old), age(old, en=True)) == ("vor 3 Tagen", "3 days ago")
    assert stamp(dt.datetime(2026, 1, 5, 12, tzinfo=dt.UTC), en=True) == "5 Jan 13:00"
    assert (filesize(1_572_864), filesize(1_572_864, en=True)) == ("1,5 MB", "1.5 MB")
    assert [compass(a) for a in (45, 90, 135)] == ["NO", "O", "SO"]
    assert [compass(a, en=True) for a in (45, 90, 135)] == ["NE", "E", "SE"]


def test_sky_labels_in_english() -> None:
    found = sky_overlay.build(_status(), AT)
    assert found is not None
    english = sky_overlay.in_english(found)
    names = {n.text for n in english.names}
    assert "Auriga" in names
    assert "Fuhrmann" in {n.text for n in found.names}
    assert [c.text for c in english.cardinals] == ["N", "E", "S", "W"]
    lay = sky_layers.build(_status(), AT)
    assert lay is not None
    lay_en = sky_layers.in_english(lay)
    assert [m.text for m in lay_en.cardinals] == ["N", "E", "S", "W"]
    assert [m.text for m in lay_en.planets] == [m.text for m in lay.planets] == ["Saturn"]
    # Names that differ: the moon (with its phase) and Mercury.
    shown = dataclasses.replace(
        lay,
        planets=[
            dataclasses.replace(lay.planets[0], text="Merkur"),
            dataclasses.replace(lay.planets[0], text="Mond 80 %"),
        ],
    )
    assert [m.text for m in sky_layers.in_english(shown).planets] == ["Mercury", "Moon 80 %"]
