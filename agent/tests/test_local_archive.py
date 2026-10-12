"""The local UI's view of the camera's own storage (roadmap L3–L6)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from allskyhub_agent.detect.events import EventStore
from allskyhub_agent.live import LiveState
from allskyhub_agent.store.images import ImageStore
from allskyhub_agent.web.archive import LocalArchive
from allskyhub_agent.web.server import WebServer
from allskyhub_protocol import FrameInfo, Mode, SkyMetrics

TZ = ZoneInfo("Europe/Vienna")
NIGHT = "20261012"


def _fill(store: ImageStore, start: datetime, n: int) -> list[FrameInfo]:
    infos: list[FrameInfo] = []
    for i in range(n):
        at = start + timedelta(minutes=10 * i)
        saved = store.save(np.zeros((90, 160, 3), dtype=np.uint8), at)
        info = FrameInfo(
            captured_at=at, night_id=saved.night_id, name=saved.name, mode=Mode.NIGHT,
            exposure_us=30_000_000, gain=200.0, mean=0.2, sun_elevation=-30.0,
            profile="sim",
        )  # fmt: skip
        store.append_index(info)
        store.append_sky(
            SkyMetrics(
                at=at,
                night_id=saved.night_id,
                cloud_cover=min(1.0, 0.1 * i),
                sqm_mag=20.5,
                stars=40,
            )
        )
        infos.append(info)
    return infos


@pytest.fixture
def setup(tmp_path: Path) -> Iterator[tuple[int, list[FrameInfo], datetime]]:
    store = ImageStore(tmp_path, TZ)
    start = datetime(2026, 10, 12, 20, 0, tzinfo=UTC)
    infos = _fill(store, start, 8)
    (store.night_dir(NIGHT) / "keogram.jpg").write_bytes(b"\xff\xd8\xff" + b"k" * 1000)
    (store.night_dir(NIGHT) / "timelapse.mp4").write_bytes(bytes(range(256)) * 4)
    web = WebServer(LiveState(), "127.0.0.1", 0, archive=LocalArchive(store, EventStore(store)))
    web.start()
    yield web.port, infos, start
    web.stop()


def _get(port: int, path: str, headers: dict[str, str] | None = None) -> tuple[int, bytes, Any]:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def test_nights_frames_sky(setup: tuple[int, list[FrameInfo], datetime]) -> None:
    port, infos, _ = setup
    status, body, _ = _get(port, "/api/nights")
    nights = json.loads(body)
    assert status == 200
    assert [n["night_id"] for n in nights] == [NIGHT]
    assert nights[0]["frames"] == 8
    assert [p["name"] for p in nights[0]["products"]] == ["keogram.jpg", "timelapse.mp4"]
    page = json.loads(_get(port, f"/api/nights/{NIGHT}/frames?offset=2&limit=3")[1])
    assert page["total"] == 8
    assert [f["name"] for f in page["frames"]] == [i.name for i in infos[2:5]]
    sky = json.loads(_get(port, f"/api/nights/{NIGHT}/sky")[1])
    assert len(sky) == 8
    assert sky[1]["cloud_cover"] == pytest.approx(0.1)
    assert json.loads(_get(port, f"/api/nights/{NIGHT}/events")[1]) == []


def test_files_and_ranges(setup: tuple[int, list[FrameInfo], datetime]) -> None:
    port, infos, _ = setup
    status, body, headers = _get(port, f"/files/{NIGHT}/{infos[0].name}")
    assert status == 200
    assert body[:3] == b"\xff\xd8\xff"
    assert headers["Content-Type"] == "image/jpeg"
    assert _get(port, f"/files/{NIGHT}/{infos[0].name}?thumb=1")[0] == 200
    assert _get(port, f"/files/{NIGHT}/products/keogram.jpg")[0] == 200
    status, body, headers = _get(
        port, f"/files/{NIGHT}/products/timelapse.mp4", {"Range": "bytes=10-19"}
    )
    assert status == 206
    assert body == bytes(range(10, 20))
    assert headers["Content-Range"] == "bytes 10-19/1024"
    assert _get(port, f"/files/{NIGHT}/products/timelapse.mp4", {"Range": "bytes=5000-"})[0] == 416


@pytest.mark.parametrize(
    "path",
    [
        f"/files/{NIGHT}/..%2Fframes.jsonl",
        f"/files/{NIGHT}/frames.jsonl",
        f"/files/{NIGHT}/products/../keogram.jpg",
        "/files/2026/x.jpg",
        f"/files/{NIGHT}/products/secret.jpg",
    ],
)
def test_nothing_outside_the_stored_files(
    setup: tuple[int, list[FrameInfo], datetime], path: str
) -> None:
    assert _get(setup[0], path)[0] == 404


def test_loop_is_the_last_hour(tmp_path: Path) -> None:
    store = ImageStore(tmp_path, TZ)
    start = datetime(2026, 10, 12, 20, 0, tzinfo=UTC)
    infos = _fill(store, start, 12)  # 20:00 .. 21:50
    loop = LocalArchive(store).loop(start + timedelta(minutes=115))
    assert [f["name"] for f in loop] == [i.name for i in infos[6:]]
