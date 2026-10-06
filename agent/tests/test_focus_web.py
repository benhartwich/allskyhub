"""Focus measure, focus mode and the local web UI (SPEC §7)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from allskyhub_agent.adapters.sim import SimCamera
from allskyhub_agent.core.clock import SimClock
from allskyhub_agent.core.exposure import AutoExposure
from allskyhub_agent.core.focus import sharpness
from allskyhub_agent.live import LiveState
from allskyhub_agent.profiles import get_profile
from allskyhub_agent.runner import Location, Runner
from allskyhub_agent.store.images import ImageStore
from allskyhub_agent.web.server import WebServer


def _stars(blur: int) -> np.ndarray:
    rng = np.random.default_rng(3)
    img = np.zeros((200, 200), dtype=np.float64)
    img[rng.integers(0, 200, 300), rng.integers(0, 200, 300)] = 255.0
    for _ in range(blur):  # 5-point box blur via slicing (np.roll's numpy 2.5 stub is untyped)
        nxt = img.copy()
        nxt[1:-1, 1:-1] = (
            img[1:-1, 1:-1] + img[:-2, 1:-1] + img[2:, 1:-1] + img[1:-1, :-2] + img[1:-1, 2:]
        ) / 5.0
        img = nxt
    out = np.empty((200, 200, 3), dtype=np.uint8)
    out[:, :, :] = np.minimum(img, 255.0).astype(np.uint8)[:, :, None]
    return out


def test_sharper_image_scores_higher() -> None:
    scores = [sharpness(_stars(b), crop_frac=1.0) for b in (0, 1, 3, 6)]
    assert scores == sorted(scores, reverse=True)
    assert scores[0] > 5 * scores[-1]


def make_runner(tmp_path: Path, live: LiveState) -> Runner:
    clock = SimClock(datetime(2026, 10, 6, 10, 0, tzinfo=UTC))
    profile = get_profile("sim")
    return Runner(
        camera=SimCamera(lambda: 1000.0, width=160, height=90, clock=clock),
        auto_exposure=AutoExposure(profile.exposure),
        store=ImageStore(tmp_path, ZoneInfo("Europe/Vienna")),
        clock=clock,
        location=Location(48.14, 14.39),
        profile=profile.id,
        live=live,
        local_tz=ZoneInfo("Europe/Vienna"),
    )


def test_focus_mode_publishes_but_does_not_store(tmp_path: Path) -> None:
    live = LiveState()
    runner = make_runner(tmp_path, live)
    runner.step()
    assert len(list(tmp_path.glob("images/*/image-*.jpg"))) == 1
    assert live.crop_jpeg() is None

    live.set_focus_mode(True)
    info = runner.step()
    assert info.name == "focus.jpg"
    assert runner.delay() == 0.0
    assert len(list(tmp_path.glob("images/*/image-*.jpg"))) == 1
    assert live.crop_jpeg() is not None
    status = live.status()
    assert status["focus_mode"] is True
    assert status["sharpness_max"] is not None


@pytest.fixture
def server(tmp_path: Path) -> Iterator[tuple[str, Runner]]:
    live = LiveState()
    runner = make_runner(tmp_path, live)
    web = WebServer(live, "127.0.0.1", 0)
    web.start()
    yield f"http://127.0.0.1:{web.port}", runner
    web.stop()


def _get(url: str) -> tuple[int, bytes, str]:
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, r.read(), r.headers["Content-Type"]


def test_web_routes(server: tuple[str, Runner]) -> None:
    base, runner = server
    status, body, ctype = _get(base + "/")
    assert status == 200
    assert b"allskyhub camera" in body
    assert ctype.startswith("text/html")

    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(base + "/api/live.jpg")
    assert exc.value.code == 404

    runner.step()
    status, body, ctype = _get(base + "/api/live.jpg")
    assert ctype == "image/jpeg"
    assert body[:2] == b"\xff\xd8"

    req = urllib.request.Request(base + "/api/focus", data=b"on", method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        assert json.loads(r.read())["focus_mode"] is True

    bad = urllib.request.Request(base + "/api/focus", data=b"maybe", method="POST")
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(bad, timeout=5)
    assert exc.value.code == 400

    _, body, _ = _get(base + "/api/status")
    assert json.loads(body)["frames"] == 1
