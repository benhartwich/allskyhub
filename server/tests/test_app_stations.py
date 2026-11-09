"""SharedSky stations in the app (api/app_stations.py): the same as "Kamera verbinden" on the
web, as JSON, and what the app needs to know before sign-in (/api/v1/public/hub)."""

from __future__ import annotations

import io
import re

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image

from allskyhub_server.mail.send import MemoryMailer
from allskyhub_server.settings import Settings
from tests.helpers import PASSWORD, make_account


def _jpeg() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (320, 240), (10, 12, 30)).save(out, "JPEG")
    return out.getvalue()


async def _token(client: httpx.AsyncClient) -> dict[str, str]:
    email = await make_account(client)
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_connect_with_indi_allsky_and_other(client: httpx.AsyncClient) -> None:
    auth = await _token(client)
    assert (await client.get("/api/v1/stations")).status_code == 401
    r = await client.post(
        "/api/v1/stations", headers=auth,
        data={"software": "indi-allsky", "name": "Gartensternwarte", "terms": "true"},
    )  # fmt: skip
    assert r.status_code == 200
    body = r.json()
    assert body["base_url"] == "http://testserver/indi-allsky"
    assert re.fullmatch(r"[0-9a-f]{64}", body["secret"])
    station = body["station"]
    assert (station["software"], station["state"]) == ("indi-allsky", "waiting")
    listed = (await client.get("/api/v1/stations", headers=auth)).json()
    assert [s["name"] for s in listed] == ["Gartensternwarte"]
    assert "secret" not in listed[0]  # once, at creation
    # Not a camera: the camera list stays empty.
    assert (await client.get("/api/v1/cameras", headers=auth)).json() == []

    no_terms = await client.post(
        "/api/v1/stations", headers=auth, data={"software": "other", "name": "Eigenbau"}
    )
    assert no_terms.status_code == 400
    other = await client.post(
        "/api/v1/stations", headers=auth,
        data={"software": "other", "name": "Eigenbau", "terms": "true"},
    )  # fmt: skip
    assert other.json()["base_url"] is None
    assert other.json()["docs_url"] == "http://testserver/entwickler/connect"

    sid = station["id"]
    wrong = await client.post(
        f"/api/v1/stations/{sid}/secret", headers=auth, json={"password": "falsch-falsch"}
    )
    assert wrong.status_code == 400
    renewed = await client.post(
        f"/api/v1/stations/{sid}/secret", headers=auth, json={"password": PASSWORD}
    )
    assert renewed.json()["secret"] != body["secret"]
    gone = await client.post(
        f"/api/v1/stations/{sid}/delete", headers=auth, json={"password": PASSWORD}
    )
    assert gone.status_code == 204
    assert (await client.get(f"/api/v1/stations/{sid}", headers=auth)).status_code == 404


async def test_by_image_address_waits_for_the_admin(
    app: FastAPI, client: httpx.AsyncClient, settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    monkeypatch.setattr(settings, "admin_emails", "admin@example.org")
    mailer: MemoryMailer = app.state.mailer
    auth = await _token(client)
    data = {"software": "image-url", "name": "Allsky Gaisberg", "terms": "true",
            "url": "https://gaisberg.example.org/allsky/image.jpg",
            "latitude": "47,8", "longitude": "3.11", "timezone": "Europe/Vienna"}  # fmt: skip
    no_proof = await client.post("/api/v1/stations", headers=auth, data=data)
    assert no_proof.status_code == 400
    assert "Nachweis" in no_proof.json()["detail"]
    english = await client.post(
        "/api/v1/stations", headers={**auth, "Accept-Language": "en"}, data=data
    )
    assert "proof" in english.json()["detail"]
    bad_url = await client.post(
        "/api/v1/stations", headers=auth,
        data={**data, "url": "ftp://x", "proof_text": "meine Seite"},
    )  # fmt: skip
    assert bad_url.status_code == 400
    r = await client.post(
        "/api/v1/stations", headers=auth, data=data,
        files={"proof": ("beweis.jpg", _jpeg(), "image/jpeg")},
    )  # fmt: skip
    assert r.status_code == 200
    body = r.json()
    assert body["secret"] is None
    assert body["station"]["state"] == "pending"
    assert body["station"]["url"] == data["url"]
    assert body["station"]["latitude"] == pytest.approx(47.8)
    assert any("wartet auf Prüfung" in m.subject for m in mailer.sent)
    sid = body["station"]["id"]
    # An image address has no secret to renew.
    renew = await client.post(
        f"/api/v1/stations/{sid}/secret", headers=auth, json={"password": PASSWORD}
    )
    assert renew.status_code == 404


async def test_someone_elses_station_is_not_found(
    client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    owner = await _token(client)
    created = await client.post(
        "/api/v1/stations",
        headers=owner,
        data={"software": "other", "name": "Meine", "terms": "true"},
    )
    sid = created.json()["station"]["id"]
    stranger = await _token(new_client)
    for r in (
        await new_client.get(f"/api/v1/stations/{sid}", headers=stranger),
        await new_client.get(f"/api/v1/stations/{sid}/image.jpg", headers=stranger),
        await new_client.post(
            f"/api/v1/stations/{sid}/delete", headers=stranger, json={"password": PASSWORD}
        ),
    ):
        assert r.status_code == 404
    assert (await new_client.get("/api/v1/stations", headers=stranger)).json() == []


async def test_hub_info_for_the_app(
    app: FastAPI, client: httpx.AsyncClient, settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    closed = (await client.get("/api/v1/public/hub")).json()
    assert closed == {"open_signup": False, "signup_url": "http://testserver/zugang"}

    class _Turnstile:
        enabled = True
        site_key = "site"

    monkeypatch.setattr(settings, "open_signup", True)
    app.state.turnstile = _Turnstile()
    opened = (await client.get("/api/v1/public/hub")).json()
    assert opened == {
        "open_signup": True,
        "signup_url": "http://testserver/konto/anlegen?weiter=sharedsky",
    }
