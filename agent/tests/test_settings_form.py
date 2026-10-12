"""Settings schema, values and the local settings form (SPEC §6.5, §6.9, roadmap L7)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from allskyhub_agent.live import LiveState
from allskyhub_agent.profiles import get_profile, with_night_limits
from allskyhub_agent.settings import AgentSettings
from allskyhub_agent.settings_schema import build_schema, check, values
from allskyhub_agent.web.server import SettingsForm, WebServer
from allskyhub_protocol import DeviceSettings, schema_problems


def test_schema_is_usable_and_follows_the_profile() -> None:
    zwo = build_schema(get_profile("zwo-asi678mc"))
    rpi = build_schema(get_profile("rpi-hq"))
    assert schema_problems(zwo) == []
    assert schema_problems(rpi) == []
    keys = [s.key for s in zwo]
    assert keys[:3] == ["location", "timezone", "camera"]
    assert {"wb_r", "wb_b"} <= set(keys)
    assert "wb_r" not in {s.key for s in rpi}  # white balance only where the camera has it
    longest = next(s for s in rpi if s.key == "night_max_exposure_s")
    assert longest.max == 200


def test_values_report_every_key_and_fit_device_settings() -> None:
    schema = build_schema(get_profile("zwo-asi678mc"))
    v = values(AgentSettings(latitude=48.1, longitude=14.4, wb_r=52), schema)
    assert (v["latitude"], v["wb_r"], v["wb_b"], v["jpeg_quality"]) == (48.1, 52, None, 90)
    assert DeviceSettings.model_validate(v).model_extra == {
        "night_max_exposure_s": None,
        "night_max_gain": None,
        "wb_r": 52,
        "wb_b": None,
        "jpeg_quality": 90,
        "detect_meteor": "normal",
        "detect_aurora": "normal",
        "detect_lightning": "normal",
        "detect_nlc": "normal",
    }


def test_check_uses_the_schema_and_the_fixed_rules() -> None:
    schema = build_schema(get_profile("zwo-asi678mc"))
    assert check(schema, {"night_max_gain": 200, "wb_r": None}) == []
    assert check(schema, {"night_max_gain": 9999, "jpeg_quality": 10}) == [
        "jpeg_quality",
        "night_max_gain",
    ]
    assert check(schema, {"timezone": "Nowhere/Town"}) == ["timezone"]
    assert check(schema, {"latitude": 48.0}) == ["latitude"]


def test_null_resets_and_missing_keys_stay(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    s = AgentSettings().with_values({"wb_r": 50, "night_max_exposure_s": 30, "jpeg_quality": 95})
    s.save(path)
    loaded = AgentSettings.load(path)
    assert (loaded.wb_r, loaded.night_max_exposure_s, loaded.jpeg_quality) == (50, 30.0, 95)
    again = loaded.with_values({"wb_r": None})
    assert (again.wb_r, again.night_max_exposure_s) == (None, 30.0)


def test_night_limits_stay_within_the_profile() -> None:
    p = get_profile("zwo-asi678mc")
    limited = with_night_limits(p, AgentSettings(night_max_exposure_s=20, night_max_gain=150))
    assert (limited.exposure.night.max_exposure_us, limited.exposure.night.max_gain) == (
        20_000_000,
        150.0,
    )
    wild = with_night_limits(p, AgentSettings(night_max_exposure_s=9999, night_max_gain=9999))
    assert wild.exposure.night == p.exposure.night
    assert with_night_limits(p, AgentSettings()).exposure == p.exposure


def _call(url: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def test_local_settings_api() -> None:
    schema = build_schema(get_profile("zwo-asi678mc"))
    current = AgentSettings(latitude=48.1, longitude=14.4)
    stored: list[Mapping[str, Any]] = []

    def change(c: Mapping[str, Any], source: str) -> list[str]:
        bad = check(schema, c)
        if not bad:
            stored.append(c)
        return bad

    form = SettingsForm(lambda: schema, lambda: values(current, schema), change)
    web = WebServer(LiveState(), "127.0.0.1", 0, settings_form=form)
    web.start()
    try:
        base = f"http://127.0.0.1:{web.port}/api/settings"
        status, data = _call(base)
        assert status == 200
        assert data["schema"][0]["key"] == "location"
        assert data["values"]["latitude"] == 48.1
        assert _call(base, {"wb_b": 120}) == (400, {"detail": "invalid", "fields": ["wb_b"]})
        assert stored == []
        assert _call(base, {"wb_b": 60, "night_max_gain": None})[0] == 202
        assert stored == [{"wb_b": 60, "night_max_gain": None}]
    finally:
        web.stop()
