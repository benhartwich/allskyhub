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
    # Benjamin, 2026-10-12: up to 300 s on the ZWO when set; empty stays 60 s.
    zwo_longest = next(s for s in zwo if s.key == "night_max_exposure_s")
    assert zwo_longest.max == 300
    assert "Leer: 60 s" in str(zwo_longest.help)


def test_values_report_every_key_and_fit_device_settings() -> None:
    schema = build_schema(get_profile("zwo-asi678mc"))
    v = values(AgentSettings(latitude=48.1, longitude=14.4, wb_r=52), schema)
    assert (v["latitude"], v["wb_r"], v["wb_b"], v["jpeg_quality"]) == (48.1, 52, None, 90)
    assert DeviceSettings.model_validate(v).model_extra == {
        "night_max_exposure_s": None,
        "night_max_gain": None,
        "moon_mode": True,
        "wb_r": 52,
        "wb_b": None,
        "jpeg_quality": 90,
        "bad_frames": True,
        "bad_dark_pct": 2,
        "bad_bright_pct": 90,
        "detect_meteor": "normal",
        "detect_aurora": "normal",
        "detect_lightning": "normal",
        "detect_nlc": "normal",
        "product_keogram": True,
        "product_startrails": True,
        "product_timelapse": True,
        "product_day_timelapse": True,
        "product_day_keogram": False,
        "product_mini_timelapse": False,
        "timelapse_fps": 25,
        "timelapse_quality": "normal",
        "keep_days": 14,
        "alpaca_enabled": False,
        "alpaca_port": 11111,
        "safe_cloud_max": 30,
        "safe_hold_min": 10,
        "nina_url": "",
        "mqtt_host": "",
        "mqtt_port": 1883,
        "mqtt_tls": False,
        "mqtt_user": "",
        "mqtt_password_set": False,
        "mqtt_prefix": "allskyhub",
        "https": False,
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
    assert wild.exposure.night.max_exposure_us == 300_000_000  # the cap, not the default
    assert wild.exposure.night.max_gain == p.exposure.night.max_gain
    long = with_night_limits(p, AgentSettings(night_max_exposure_s=180))
    assert long.exposure.night.max_exposure_us == 180_000_000
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


def test_night_product_and_retention_settings(tmp_path: Path) -> None:
    # Roadmap L9, SPEC §4.5.
    schema = build_schema(get_profile("zwo-asi678mc"))
    assert check(schema, {"keep_days": 60, "timelapse_fps": 30, "product_keogram": False}) == []
    assert check(schema, {"keep_days": 0}) == ["keep_days"]
    assert check(schema, {"timelapse_quality": "huge"}) == ["timelapse_quality"]
    path = tmp_path / "settings.json"
    AgentSettings().with_values(
        {"keep_days": 60, "product_timelapse": False, "timelapse_quality": "high"}
    ).save(path)
    s = AgentSettings.load(path)
    assert (s.keep_days, s.product_timelapse, s.timelapse_quality) == (60, False, "high")
    assert (s.product_keogram, s.timelapse_fps) == (True, 25)


def test_mqtt_password_is_write_only(tmp_path: Path) -> None:
    """SPEC §6.9/§7.7: the secret is never reported, only whether it is set; an empty
    value keeps it, null deletes it; the settings file is readable by the agent only."""
    from allskyhub_agent import settings_schema as ss
    from allskyhub_agent.profiles import get_profile
    from allskyhub_agent.settings import AgentSettings

    schema = ss.build_schema(get_profile("sim"))
    s = AgentSettings().with_values({"mqtt_host": "ha.local", "mqtt_password": "geheim"})
    v = ss.values(s, schema)
    assert "mqtt_password" not in v
    assert v["mqtt_password_set"] is True
    assert s.with_values({"mqtt_password": ""}).mqtt_password == "geheim"  # noqa: S105
    assert s.with_values({"mqtt_password": None}).mqtt_password is None
    assert "mqtt_password" in ss.keys(schema)
    assert "mqtt_password_set" not in ss.keys(schema)
    path = tmp_path / "settings.json"
    s.save(path)
    assert path.stat().st_mode & 0o777 == 0o600
    assert AgentSettings.load(path).mqtt_password == "geheim"  # noqa: S105
    assert ss.check(schema, {"mqtt_prefix": "a/#"}) == ["mqtt_prefix"]
    assert ss.check(schema, {"mqtt_host": "mqtt://x"}) == ["mqtt_host"]
    assert ss.check(schema, {"mqtt_host": "192.168.1.5", "mqtt_prefix": "home/sky"}) == []
