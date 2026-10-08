"""The owner's verdict on detections ("kein Meteor") and the export for tuning."""

from __future__ import annotations

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fake_device import FakeDevice
from tests.helpers import PASSWORD, csrf_from, make_account, owner_email
from tests.test_web_events import EVENT, NIGHT, record
from tests.ws_helpers import paired_token


async def test_labels_in_app_api_and_export(app: FastAPI, client: httpx.AsyncClient) -> None:
    dev = FakeDevice()
    await paired_token(client, dev)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        db.add(record(dev.device_id))
        await db.commit()
    r = await client.post(
        "/api/v1/auth/login",
        json={"email": await owner_email(app, dev.device_id), "password": PASSWORD},
    )
    auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
    api = f"/api/v1/cameras/{dev.device_id}"
    label = f"{api}/events/{NIGHT}/{EVENT}/label"

    assert (await client.put(label, json={"label": "nonsense"}, headers=auth)).status_code == 400
    marked = await client.put(label, json={"label": "false_positive"}, headers=auth)
    assert marked.json()["label"] == "false_positive"
    assert [e["id"] for e in (await client.get(f"{api}/events", headers=auth)).json()] == [EVENT]
    hidden = await client.get(f"{api}/events", params={"hide_false": "true"}, headers=auth)
    assert hidden.json() == []

    export = (await client.get(f"{api}/events/export", headers=auth)).json()
    assert export[0]["id"] == EVENT
    assert export[0]["label"] == "false_positive"
    assert export[0]["labelled_at"] is not None
    assert export[0]["data"]["length_px"] == 412
    only = await client.get(f"{api}/events/export", params={"labelled_only": "true"}, headers=auth)
    assert len(only.json()) == 1

    cleared = await client.put(label, json={"label": None}, headers=auth)
    assert cleared.json()["label"] is None
    only = await client.get(f"{api}/events/export", params={"labelled_only": "true"}, headers=auth)
    assert only.json() == []


async def test_labels_in_web(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    dev = FakeDevice()
    await paired_token(client, dev)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        db.add(record(dev.device_id))
        await db.commit()
    base = f"/cameras/{dev.device_id}"
    detail = f"{base}/events/{NIGHT}/{EVENT}"

    page = await client.get(detail)
    assert "Kein Meteor (z. B. Flugzeug)" in page.text
    r = await client.post(
        f"{detail}/label", data={"label": "false_positive", "csrf_token": csrf_from(page.text)}
    )
    assert r.status_code == 303
    assert "Als „kein Meteor“ markiert" in (await client.get(detail)).text
    # Hidden from the camera page and the list, unless asked for.
    assert f'href="{detail}"' not in (await client.get(base)).text
    assert f'href="{detail}"' not in (await client.get(f"{base}/events")).text
    assert "aussortiert" in (await client.get(f"{base}/events", params={"all": "true"})).text

    export = await client.get(f"{base}/events.json")
    assert export.headers["content-disposition"].startswith("attachment")
    assert export.json()[0]["label"] == "false_positive"

    await make_account(new_client)
    other = await new_client.get("/account")
    r = await new_client.post(
        f"{detail}/label", data={"label": "confirmed", "csrf_token": csrf_from(other.text)}
    )
    assert r.status_code == 404
    assert (await new_client.get(f"{base}/events.json")).status_code == 404
