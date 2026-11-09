"""SharedSky pages (SPEC §10): "Kamera verbinden", a station's page, the admin's check of
stations by their image address (§10.7), /mitmachen and /entwickler/connect."""

from __future__ import annotations

import io
import re

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices import image_url
from allskyhub_server.mail.send import MemoryMailer
from allskyhub_server.models import Device, StationSource
from allskyhub_server.settings import Settings
from tests.helpers import PASSWORD, csrf_from, make_account


def _jpeg() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (320, 240), (10, 12, 30)).save(out, "JPEG")
    return out.getvalue()


async def _new(client: httpx.AsyncClient, software: str, **fields: str) -> httpx.Response:
    page = await client.get("/stationen/neu", params={"software": software})
    assert page.status_code == 200
    data = {"csrf_token": csrf_from(page.text), "software": software, "terms": "true", **fields}
    return await client.post("/stationen/neu", data=data, follow_redirects=True)


async def test_indi_allsky_and_other_software(app: FastAPI, client: httpx.AsyncClient) -> None:
    await make_account(client)
    assert "Kamera verbinden" in (await client.get("/")).text
    r = await _new(client, "indi-allsky", name="Gartensternwarte")
    assert r.status_code == 200
    station_id = r.url.path.rsplit("/", 1)[1]
    assert "http://testserver/indi-allsky" in r.text
    assert station_id in r.text
    secret = re.search(r'data-copy="([0-9a-f]{64})"', r.text)
    assert secret
    again = await client.get(f"/stationen/{station_id}")
    assert secret[1] not in again.text  # shown once
    assert "wartet auf eine klare Nacht" in again.text
    assert "Gartensternwarte" in (await client.get("/")).text  # listed with the cameras

    # A station has no camera pages: /cameras leads to its own page.
    moved = await client.get(f"/cameras/{station_id}")
    assert (moved.status_code, moved.headers["location"]) == (303, f"/stationen/{station_id}")
    assert (await client.get(f"/cameras/{station_id}/live")).status_code == 404

    # A new secret with the password.
    page = await client.get(f"/stationen/{station_id}")
    wrong = await client.post(
        f"/stationen/{station_id}/geheimnis",
        data={"csrf_token": csrf_from(page.text), "password": "falsch-falsch"},
        follow_redirects=True,
    )
    assert "Dein Passwort stimmt nicht" in wrong.text
    renewed = await client.post(
        f"/stationen/{station_id}/geheimnis",
        data={"csrf_token": csrf_from(page.text), "password": PASSWORD}, follow_redirects=True,
    )  # fmt: skip
    new_secret = re.search(r'data-copy="([0-9a-f]{64})"', renewed.text)
    assert new_secret
    assert new_secret[1] != secret[1]

    other = await _new(client, "other", name="Eigenbau")
    assert "/entwickler/connect" in other.text
    assert re.search(r'data-copy="[0-9a-f]{64}"', other.text)

    # Removing: images, place and secret go; the page is gone.
    gone = await client.post(
        f"/stationen/{station_id}/entfernen",
        data={"csrf_token": csrf_from(page.text), "password": PASSWORD},
    )
    assert (gone.status_code, gone.headers["location"]) == (303, "/")
    assert (await client.get(f"/stationen/{station_id}")).status_code == 404
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        device = await db.get(Device, station_id)
        assert device is not None
        assert (device.owner_id, device.station_secret_hash, device.last_status) == (
            None,
            None,
            None,
        )


async def test_others_cannot_see_a_station(
    client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    await make_account(client)
    r = await _new(client, "other", name="Meine")
    station_id = r.url.path.rsplit("/", 1)[1]
    await make_account(new_client)
    assert (await new_client.get(f"/stationen/{station_id}")).status_code == 404
    assert (await new_client.get(f"/stationen/{station_id}/bild.jpg")).status_code == 404


async def test_image_address_checked_by_an_admin(
    app: FastAPI,
    client: httpx.AsyncClient,
    new_client: httpx.AsyncClient,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await make_account(client)
    fields = {"name": "Allsky Musterberg", "url": "https://cam.example.org/allsky/image.jpg",
              "latitude": "48,30", "longitude": "4.29", "elevation_m": "450",
              "timezone": "Europe/Vienna"}  # fmt: skip
    missing = await _new(client, "image-url", **fields)
    assert "Bitte einen Nachweis angeben" in missing.text
    bad = await _new(client, "image-url", **(fields | {"url": "http://cam.example.org:8080/x.jpg",
                                                       "proof_text": "x"}))  # fmt: skip
    assert "Port 80 oder 443" in bad.text
    page = await client.get("/stationen/neu", params={"software": "image-url"})
    r = await client.post(
        "/stationen/neu",
        data={"csrf_token": csrf_from(page.text), "software": "image-url", "terms": "true",
              "proof_text": "Siehe Screenshot meiner Allsky-Einstellungen.", **fields},
        files={"proof": ("einstellungen.jpg", _jpeg(), "image/jpeg")},
        follow_redirects=True,
    )  # fmt: skip
    assert r.status_code == 200
    assert "wartet auf Prüfung" in r.text
    station_id = r.url.path.rsplit("/", 1)[1]
    assert "Neues Geheimnis" not in r.text  # nothing to send, so no secret

    # The admin's check: request, proof, the image fetched now, the place.
    admin = await make_account(new_client)
    monkeypatch.setattr(settings, "admin_emails", f"{admin},{owner}x")
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["host"])
        return httpx.Response(200, content=_jpeg(), headers={"content-type": "image/jpeg"})

    async def resolve(host: str, port: int) -> list[str]:
        return ["93.184.216.34"]

    app.state.image_fetcher = image_url.Fetcher(
        resolve=resolve, transport=httpx.MockTransport(handler)
    )
    listed = await new_client.get("/admin/stationen")
    assert "Allsky Musterberg" in listed.text
    assert "wartet auf Prüfung" in listed.text
    detail = await new_client.get(f"/admin/stationen/{station_id}")
    assert detail.status_code == 200
    assert "data:image/jpeg;base64," in detail.text
    assert seen == ["cam.example.org"]
    assert "Siehe Screenshot" in detail.text
    proof = await new_client.get(f"/admin/stationen/{station_id}/nachweis.jpg")
    assert proof.headers["content-type"] == "image/jpeg"
    assert (await client.get(f"/admin/stationen/{station_id}")).status_code == 404  # not admin

    csrf = csrf_from(detail.text)
    no_reason = await new_client.post(
        f"/admin/stationen/{station_id}/ablehnen",
        data={"csrf_token": csrf, "password": PASSWORD}, follow_redirects=True,
    )  # fmt: skip
    assert "Bitte einen Grund angeben" in no_reason.text
    mailer: MemoryMailer = app.state.mailer
    sent = len(mailer.sent)
    done = await new_client.post(
        f"/admin/stationen/{station_id}/freigeben",
        data={"csrf_token": csrf, "password": PASSWORD}, follow_redirects=True,
    )  # fmt: skip
    assert "Station freigegeben" in done.text
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        source = await db.scalar(select(StationSource).where(StationSource.device_id == station_id))
        device = await db.get(Device, station_id)
    assert source is not None
    assert device is not None
    assert source.state == "approved"
    assert source.next_fetch_at is not None
    assert (device.last_status or {})["settings"]["latitude"] == 48.3
    owner_mail = [m for m in mailer.sent[sent:] if m.to == owner]
    assert owner_mail
    assert "freigegeben" in owner_mail[0].subject


async def test_public_pages(client: httpx.AsyncClient) -> None:
    join = await client.get("/mitmachen")
    assert join.status_code == 200
    assert "Deine Allsky-Kamera sieht mehr" in join.text
    assert "/zugang" in join.text  # open sign-up is off in the tests
    assert 'href="/mitmachen"' in join.text  # the footer
    docs = await client.get("/entwickler/connect")
    assert docs.status_code == 200
    assert "http://testserver/api/connect/v1" in docs.text
    assert "Testmodus" in docs.text
    assert "http://testserver/indi-allsky" in docs.text
