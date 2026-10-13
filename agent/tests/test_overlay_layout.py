"""The overlay layout (SPEC §7 Overlay)."""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

from allskyhub_agent.live import LiveState
from allskyhub_agent.web.overlay import DEFAULT, OverlayLayout, parse
from allskyhub_agent.web.server import WebServer


def test_parse() -> None:
    assert parse(DEFAULT) == DEFAULT
    ok = parse({"corners": {"tl": ["text1", "time"]}, "texts": ["Sternwarte"], "size": "large"})
    assert ok is not None
    assert ok["corners"] == {"tl": ["text1", "time"], "tr": [], "bl": [], "br": []}
    assert ok["texts"] == ["Sternwarte", ""]
    assert ok["compass"] is True
    bads: list[object] = [
        {"corners": {"tl": ["time", "time"]}},  # a field twice
        {"corners": {"tl": ["weather"]}},  # unknown
        {"corners": {}, "texts": ["x" * 61]},
        {"corners": {}, "size": "huge"},
        {"corners": {}, "compass": "yes"},
        [1, 2],
    ]
    for bad in bads:
        assert parse(bad) is None, bad


def test_route_keeps_it_per_camera(tmp_path: Path) -> None:
    store = OverlayLayout(tmp_path / "overlay.json")
    assert store.get() == DEFAULT
    web = WebServer(LiveState(), "127.0.0.1", 0, overlay=store,
                    status_extra=lambda: {"moon": {"illumination": 0.5}})  # fmt: skip
    web.start()
    try:
        base = f"http://127.0.0.1:{web.port}"
        body = json.dumps({"corners": {"br": ["sqm"]}, "size": "small"}).encode()
        req = urllib.request.Request(base + "/api/overlay", data=body, method="POST")
        with urllib.request.urlopen(req) as r:
            assert json.loads(r.read())["corners"]["br"] == ["sqm"]
        with urllib.request.urlopen(base + "/api/overlay") as r:
            assert json.loads(r.read())["size"] == "small"
        with urllib.request.urlopen(base + "/api/status") as r:
            assert json.loads(r.read())["moon"] == {"illumination": 0.5}
    finally:
        web.stop()
    assert OverlayLayout(tmp_path / "overlay.json").get()["corners"]["br"] == ["sqm"]
