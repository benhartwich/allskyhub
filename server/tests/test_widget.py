"""Home-screen widgets: a read-only key per camera (roadmap N6)."""

from __future__ import annotations

import httpx
from fastapi import FastAPI

from allskyhub_protocol import FrameVariant
from allskyhub_server.devices.images import ImageStore
from tests.test_app_manage import _camera  # pyright: ignore[reportPrivateUsage]


async def test_widget_key_reads_only_its_camera(app: FastAPI, client: httpx.AsyncClient) -> None:
    device_id, auth = await _camera(client)
    key = (await client.post(f"/api/v1/cameras/{device_id}/widget", headers=auth)).json()["token"]
    widget = {"Authorization": f"Bearer {key}"}

    state = (await client.get("/api/v1/widget", headers=widget)).json()
    assert state["name"] == "Garten"
    assert state["online"] is False
    assert (await client.get("/api/v1/widget/image.jpg", headers=widget)).status_code == 404
    store: ImageStore = app.state.images
    path = store.path(device_id, FrameVariant.THUMB)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xd8\xffthumb")
    image = await client.get("/api/v1/widget/image.jpg", headers=widget)
    assert image.content == b"\xff\xd8\xffthumb"

    # Nothing else: the key is no app token.
    assert (await client.get("/api/v1/cameras", headers=widget)).status_code == 401
    assert (await client.get("/api/v1/widget")).status_code == 401

    # A new key replaces the old one; deleting it ends the widget.
    again = (await client.post(f"/api/v1/cameras/{device_id}/widget", headers=auth)).json()
    assert (await client.get("/api/v1/widget", headers=widget)).status_code == 401
    widget = {"Authorization": f"Bearer {again['token']}"}
    assert (await client.get("/api/v1/widget", headers=widget)).status_code == 200
    r = await client.delete(f"/api/v1/cameras/{device_id}/widget", headers=auth)
    assert r.status_code == 204
    assert (await client.get("/api/v1/widget", headers=widget)).status_code == 401


async def test_no_key_for_someone_elses_camera(
    client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    device_id, _ = await _camera(client)
    _, other = await _camera(new_client)
    r = await new_client.post(f"/api/v1/cameras/{device_id}/widget", headers=other)
    assert r.status_code == 404
