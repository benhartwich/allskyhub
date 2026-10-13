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


def test_host_allow_list() -> None:
    """SPEC §7.4 Addresses: against DNS rebinding."""
    from allskyhub_agent.web.server import host_allowed

    for ok in (
        "192.168.1.5",
        "192.168.1.5:8080",
        "[fe80::1]:8443",
        "localhost:8080",
        "allskyhub-abcd.local",
        "allskyhub-abcd.local.",
        "allsky",
        "allsky:8080",
    ):
        assert host_allowed(ok, []), ok  # fmt: skip
    for bad in ("evil.example", "evil.example:8080", "192.168.1.5.nip.io", "", "allsky.fritz.box"):
        assert not host_allowed(bad, []), bad
    assert host_allowed("Allsky.Fritz.Box:8080", ["allsky.fritz.box"])


def test_local_hostnames_setting() -> None:
    from allskyhub_agent.settings import AgentSettings, valid_hostnames

    assert valid_hostnames("allsky.fritz.box, cam.example.org")
    assert valid_hostnames("")
    assert not valid_hostnames("http://x")
    assert not valid_hostnames("a b c d e f g h i j k")  # more than 10
    s = AgentSettings().with_values({"local_hostnames": "Allsky.Fritz.Box,cam.example.org"})
    assert s.local_hostnames == "allsky.fritz.box cam.example.org"


def test_mqtt_password_stays_with_its_broker() -> None:
    """SPEC §7.7: a new broker gets no stored password."""
    from allskyhub_agent.settings import AgentSettings

    pw, pw2 = "broker-1", "broker-2"
    s = AgentSettings().with_values({"mqtt_host": "ha.local", "mqtt_password": pw})
    assert s.mqtt_password == pw
    assert s.with_values({"mqtt_prefix": "sky"}).mqtt_password == pw
    assert s.with_values({"mqtt_host": "ha.local"}).mqtt_password == pw  # unchanged
    assert s.with_values({"mqtt_host": "evil.example"}).mqtt_password is None
    assert s.with_values({"mqtt_port": 8883}).mqtt_password is None
    moved = s.with_values({"mqtt_host": "new.local", "mqtt_password": pw2})
    assert moved.mqtt_password == pw2


def test_setup_needs_https_hub() -> None:
    from pydantic import ValidationError

    from allskyhub_agent.web.server import SetupNetworkBody

    with pytest.raises(ValidationError):
        SetupNetworkBody.model_validate(
            {"ssid": "x", "country": "DE", "hub_url": "http://allskyhub.org"}
        )
