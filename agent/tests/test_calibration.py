"""Detection setup in the local UI: detectors, drawn sky-mask areas, orientation (roadmap L8)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import cv2
import numpy as np
import pytest

from allskyhub_agent.calib.solve import Solution
from allskyhub_agent.detect import tuning
from allskyhub_agent.detect.events import DetectionWorker
from allskyhub_agent.detect.meteor import MeteorConfig
from allskyhub_agent.live import LiveState
from allskyhub_agent.process.skymask import MaskEdits, SkyMask, parse_edits
from allskyhub_agent.profiles import get_profile
from allskyhub_agent.settings import AgentSettings
from allskyhub_agent.settings_schema import build_schema, check, values
from allskyhub_agent.store.images import ImageStore
from allskyhub_agent.web.calibration import LocalCalibration
from allskyhub_agent.web.server import WebServer

W, H = 640, 360
SQUARE = [[0.0, 0.0], [0.25, 0.0], [0.25, 0.25], [0.0, 0.25]]


def test_detector_levels_are_settings() -> None:
    schema = build_schema(get_profile("zwo-asi678mc"))
    keys = [s.key for s in schema]
    assert {"detect_meteor", "detect_aurora", "detect_lightning", "detect_nlc"} <= set(keys)
    assert values(AgentSettings(), schema)["detect_meteor"] == "normal"
    assert check(schema, {"detect_meteor": "off"}) == []
    assert check(schema, {"detect_meteor": "loud"}) == ["detect_meteor"]
    s = AgentSettings().with_values({"detect_meteor": "high", "detect_nlc": "off"})
    assert (s.detect_meteor, s.detect_nlc, s.detect_aurora) == ("high", "off", "normal")
    assert tuning.enabled(s) == {"meteor": True, "aurora": True, "lightning": True, "nlc": False}


def test_detector_settings_survive_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    AgentSettings().with_values({"detect_lightning": "low"}).save(path)
    assert AgentSettings.load(path).detect_lightning == "low"
    path.write_text(json.dumps({"detect_lightning": "loud"}))
    assert AgentSettings.load(path).detect_lightning == "normal"


def test_levels_move_only_the_deciding_thresholds() -> None:
    normal, high, low = (tuning.meteor_config(x) for x in ("normal", "high", "low"))
    assert normal == MeteorConfig()
    assert high.diff_threshold < normal.diff_threshold < low.diff_threshold
    assert high.min_length_px < normal.min_length_px < low.min_length_px
    assert high.cloud_fraction == normal.cloud_fraction
    assert (
        tuning.lightning_config("high").min_area_frac < tuning.lightning_config("low").min_area_frac
    )
    assert tuning.aurora_config("high").min_index < tuning.aurora_config("low").min_index
    assert tuning.nlc_config("high").min_index < tuning.nlc_config("low").min_index


def test_switched_off_detector_sees_no_frame(tmp_path: Path) -> None:
    worker = DetectionWorker(ImageStore(tmp_path, ZoneInfo("UTC")), enabled={"meteor": False})
    assert worker._on == {"meteor": False, "lightning": True, "aurora": True}  # pyright: ignore[reportPrivateUsage]


def test_parse_edits() -> None:
    edits = parse_edits({"exclude": [SQUARE], "include": []})
    assert edits is not None
    assert len(edits.exclude) == 1
    assert parse_edits({"exclude": [SQUARE[:2]]}) is None  # not an area
    assert parse_edits({"exclude": [[[0.0, 0.0], [2.0, 0.0], [0.5, 0.5]]]}) is None
    assert parse_edits({"exclude": [SQUARE], "other": []}) is None
    assert parse_edits({"exclude": [SQUARE] * 31}) is None
    assert parse_edits([]) is None


def test_drawn_areas_edit_the_mask_and_survive(tmp_path: Path) -> None:
    path = tmp_path / "skymask.png"
    mask = SkyMask(path, fallback_frac=0.48)
    assert mask.get(H, W) is None  # nothing learned, nothing drawn: the caller's circle
    v = mask.version
    mask.set_edits(MaskEdits(exclude=(((0.4, 0.4), (0.6, 0.4), (0.6, 0.6), (0.4, 0.6)),)))
    assert mask.version > v
    m = mask.get(H, W)
    assert m is not None
    assert not m[H // 2, W // 2]  # left out although inside the circle
    assert m[H // 2, int(W * 0.3)]  # the rest of the circle stays sky
    assert not m[0, 0]  # outside the circle
    # A learned mask keeps the drawn areas on top; an included corner wins.
    cv2.imwrite(str(path), np.full((540, 960), 255, dtype=np.uint8))
    again = SkyMask(path)
    again.set_edits(MaskEdits(include=(), exclude=again.edits.exclude))
    m2 = again.get(H, W)
    assert m2 is not None
    assert m2[0, 0]
    assert not m2[H // 2, W // 2]
    again.forget()
    assert not path.exists()
    assert not again.learned
    assert again.get(H, W) is not None  # the drawn area is still there


@pytest.fixture
def web(tmp_path: Path) -> Iterator[tuple[int, Path]]:
    cal = tmp_path / "calibration"
    cal.mkdir()
    sol = Solution(cx=W / 2, cy=H / 2, a1=160.0, a3=0.0, rot=30.0, flip=-1.0, tilt_east=0.0,
                   tilt_north=0.0, stars=40, rms_px=1.0, rms_deg=0.2,
                   width=W, height=H)  # fmt: skip
    (cal / "orientation.json").write_text(
        json.dumps(asdict(sol) | {"solved_at": "2026-10-11T22:00:00+00:00"})
    )
    sky_mask = SkyMask(cal / "skymask.png", fallback_frac=0.48)
    calibration = LocalCalibration(sky_mask, cal / "orientation.json")
    server = WebServer(LiveState(), "127.0.0.1", 0, calibration=calibration)
    server.start()
    yield server.port, cal
    server.stop()


def _call(port: int, path: str, body: object = None) -> tuple[int, Any]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data)
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read()
            ctype = r.headers.get("Content-Type", "")
            return r.status, json.loads(raw) if ctype == "application/json" else raw
    except urllib.error.HTTPError as e:
        return e.code, None


def test_orientation_overlay(web: tuple[int, Path]) -> None:
    port, _ = web
    status, info = _call(port, "/api/calibration")
    assert status == 200
    o = info["orientation"]
    assert (o["north_deg"], o["mirrored"], o["stars"]) == (30.0, True, 40)
    assert o["solved_at"] == "2026-10-11T22:00:00Z"
    ov = o["overlay"]
    assert ov["zenith"] == [0.5, 0.5]
    n = next(p for p in ov["points"] if p["label"] == "N")
    # North 30° clockwise from up: right of centre and above it.
    assert n["x"] > 0.5
    assert n["y"] < 0.5
    assert len(ov["horizon"]) == 73
    assert len(ov["rings"]) == 2
    assert info["mask_learned_at"] is None


def test_draw_save_and_relearn(web: tuple[int, Path]) -> None:
    port, cal = web
    assert _call(port, "/api/skymask.png?w=320&h=180")[0] == 404  # nothing to shade yet
    status, info = _call(port, "/api/calibration/mask", {"exclude": [SQUARE], "include": []})
    assert status == 200
    assert info["edits"]["exclude"] == [SQUARE]
    assert json.loads((cal / "skymask_edits.json").read_text())["exclude"] == [SQUARE]
    status, png = _call(port, "/api/skymask.png?w=320&h=180")
    assert status == 200
    shade = cv2.imdecode(np.ndarray((len(png),), dtype=np.uint8, buffer=png), cv2.IMREAD_UNCHANGED)
    assert shade is not None
    assert shade.shape == (180, 320, 4)
    assert shade[10, 10, 3] > 0  # the drawn corner is shaded
    assert shade[90, 160, 3] == 0  # the middle is sky
    assert _call(port, "/api/calibration/mask", {"exclude": "everything"})[0] == 400
    cv2.imwrite(str(cal / "skymask.png"), np.full((540, 960), 255, dtype=np.uint8))
    status, info = _call(port, "/api/calibration/relearn", {})
    assert status == 200
    assert not (cal / "skymask.png").exists()


def test_sky_overlay(web: tuple[int, Path]) -> None:
    # Roadmap L11: the same constellations as on the hub (packages/sky), for an image's time.
    port, cal = web
    assert _call(port, "/api/sky-overlay?at=2026-10-12T21:00:00Z")[0] == 404  # no location
    calibration = LocalCalibration(SkyMask(cal / "skymask.png"), cal / "orientation.json",
                                   lambda: (48.14, 14.39))  # fmt: skip
    ov = calibration.sky_overlay(datetime(2026, 10, 12, 21, 0, tzinfo=UTC), (48.14, 14.39))
    assert ov is not None
    assert (ov["width"], ov["height"]) == (W, H)
    assert ov["paths"]
    assert [c["text"] for c in ov["cardinals"]] == ["N", "O", "S", "W"]
