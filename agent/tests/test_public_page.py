"""The public page from the local UI (SPEC §6.8, roadmap L10)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from allskyhub_agent.hub.identity import DeviceIdentity
from allskyhub_agent.hub.public import HubUnreachable, PublicPage, PublicRejected
from allskyhub_agent.live import LiveState
from allskyhub_agent.web.server import WebServer
from allskyhub_protocol import PublicProfile, PublicState


class FakeHub:
    def __init__(self) -> None:
        self.state = PublicState(enabled=False, url=None, profile=PublicProfile())
        self.tokens = 0
        self.down = False
        self.expire_next = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("down", request=request)
        path = request.url.path
        if path == "/device/v1/challenge":
            return httpx.Response(200, json={"nonce": "n" * 32, "expires_in": 60})
        if path == "/device/v1/token":
            self.tokens += 1
            return httpx.Response(200, json={"access_token": f"t{self.tokens}",
                                             "expires_in": 3600, "hub_keys": []})  # fmt: skip
        assert path == "/device/v1/public"
        if request.headers["Authorization"] != f"Bearer t{self.tokens}" or self.expire_next:
            self.expire_next = False
            return httpx.Response(401, json={"detail": "Invalid token"})
        if request.method == "PUT":
            body: dict[str, Any] = json.loads(request.content)
            if len(body.get("profile", {}).get("title", "")) > 100:
                return httpx.Response(422, json={"detail": "Der Titel ist zu lang."})
            if body.get("profile") is not None:
                self.state = self.state.model_copy(
                    update={"profile": PublicProfile.model_validate(body["profile"])}
                )
            if body.get("enabled") is True:
                self.state = self.state.model_copy(
                    update={"enabled": True, "url": "https://hub.test/sky/abc"}
                )
        return httpx.Response(200, content=self.state.model_dump_json())


@pytest.fixture
def hub(tmp_path: Path) -> tuple[FakeHub, PublicPage]:
    fake = FakeHub()
    ident = DeviceIdentity.load_or_create(tmp_path / "device.key")
    page = PublicPage(
        "https://hub.test",
        ident,
        client=lambda: httpx.Client(
            base_url="https://hub.test", transport=httpx.MockTransport(fake.handle)
        ),
    )
    return fake, page


def test_get_put_and_token_reuse(hub: tuple[FakeHub, PublicPage]) -> None:
    fake, page = hub
    assert page.get().enabled is False
    st = page.put({"enabled": True, "profile": {"title": "Musterdorf"}})
    assert (st.enabled, st.url, st.profile.title) == (
        True,
        "https://hub.test/sky/abc",
        "Musterdorf",
    )
    assert fake.tokens == 1  # kept between calls
    fake.expire_next = True
    assert page.get().enabled
    assert fake.tokens == 2  # a refused token is replaced once


def test_rejected_and_unreachable(hub: tuple[FakeHub, PublicPage]) -> None:
    fake, page = hub
    with pytest.raises(PublicRejected) as exc:
        page.put({"profile": {"title": "x" * 101}})
    assert exc.value.detail == "Der Titel ist zu lang."
    page.get()
    fake.down = True
    with pytest.raises(HubUnreachable):
        page.get()
    assert page.last is not None  # kept for the read-only view


@pytest.fixture
def web(hub: tuple[FakeHub, PublicPage]) -> Iterator[tuple[int, FakeHub]]:
    fake, page = hub
    server = WebServer(LiveState(), "127.0.0.1", 0, public=page)
    server.start()
    yield server.port, fake
    server.stop()


def _call(port: int, body: object = None) -> tuple[int, Any]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/public", data=data)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_local_routes(web: tuple[int, FakeHub]) -> None:
    port, fake = web
    assert _call(port, {"profile": {"title": "x" * 101}}) == (
        422,
        {"detail": "Der Titel ist zu lang."},
    )
    status, st = _call(port, {"enabled": True})
    assert status == 200
    assert st["online"]
    assert st["url"] == "https://hub.test/sky/abc"
    fake.down = True
    status, st = _call(port)
    assert status == 200
    assert not st["online"]  # the last known state, read-only
    assert st["enabled"]
    assert _call(port, {"enabled": False})[0] == 503
    assert _call(port, ["not", "an", "object"])[0] == 400
