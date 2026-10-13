"""The local UI refuses cross-site posts and framing (security review 2026-10-13)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from allskyhub_agent.live import LiveState
from allskyhub_agent.web.overlay import OverlayLayout
from allskyhub_agent.web.server import WebServer


def _post(url: str, headers: dict[str, str]) -> int:
    body = json.dumps({"corners": {"br": ["sqm"]}}).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status
    except urllib.error.HTTPError as exc:
        return exc.code


@pytest.fixture
def base(tmp_path: Path) -> Iterator[str]:
    web = WebServer(LiveState(), "127.0.0.1", 0, overlay=OverlayLayout(tmp_path / "o.json"))
    web.start()
    try:
        yield f"http://127.0.0.1:{web.port}"
    finally:
        web.stop()


def test_cross_site_post_is_refused(base: str) -> None:
    host = base.removeprefix("http://")
    url = base + "/api/overlay"
    assert _post(url, {"Origin": "http://evil.example"}) == 403
    assert _post(url, {"Origin": "null"}) == 403
    assert _post(url, {"Origin": base}) == 200  # the camera's own page
    assert _post(url, {}) == 200  # the app or curl: no Origin
    # Behind the local reverse proxy the browser's address is in X-Forwarded-Host.
    proxied = {"Origin": "https://cam.example.org", "X-Forwarded-Host": "cam.example.org"}
    assert _post(url, proxied) == 200
    assert _post(url, {"Origin": "https://cam.example.org", "Host": host}) == 403


def test_pages_cannot_be_framed(base: str) -> None:
    with urllib.request.urlopen(base + "/api/overlay") as r:
        assert r.headers["X-Frame-Options"] == "DENY"
        assert r.headers["X-Content-Type-Options"] == "nosniff"


def test_wifi_credentials_cannot_add_lines() -> None:
    from pydantic import ValidationError

    from allskyhub_agent.web.server import SetupNetworkBody

    wpa = "geheim123!"
    ok = SetupNetworkBody.model_validate({"ssid": "Sternwarte", "password": wpa, "country": "DE"})
    assert ok.password == wpa
    base = {"ssid": "x", "password": wpa, "country": "DE"}
    for bad in ({"password": wpa + "\n802-1x.password:x"}, {"ssid": "a\nb"}):
        with pytest.raises(ValidationError):
            SetupNetworkBody.model_validate(base | bad)


def test_settings_never_written_through_a_planted_link(tmp_path: Path) -> None:
    from allskyhub_agent.settings import AgentSettings

    victim = tmp_path / "victim"
    victim.write_text("keep")
    (tmp_path / "settings.tmp").symlink_to(victim)  # the old fixed temp name
    AgentSettings(hub_url="https://allskyhub.org").save(tmp_path / "settings.json")
    assert victim.read_text() == "keep"
    assert (
        json.loads((tmp_path / "settings.json").read_text())["hub_url"] == "https://allskyhub.org"
    )
    assert not list(tmp_path.glob(".settings-*"))  # no temp file left
