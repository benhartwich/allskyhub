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


def _fill(store: ImageStore, start: datetime, n: int, bad: tuple[int, ...] = ()) -> list[FrameInfo]:
    infos: list[FrameInfo] = []
    for i in range(n):
        at = start + timedelta(minutes=10 * i)
        saved = store.save(np.zeros((90, 160, 3), dtype=np.uint8), at)
        info = FrameInfo(
            captured_at=at, night_id=saved.night_id, name=saved.name, mode=Mode.NIGHT,
            exposure_us=30_000_000, gain=200.0, mean=0.2, sun_elevation=-30.0,
            profile="sim", bad="bright" if i in bad else None,
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


def test_bad_frames_hidden_unless_asked(tmp_path: Path) -> None:
    """SPEC §4.9: bad frames are left out of the grid, the loop and the cover."""
    store = ImageStore(tmp_path, TZ)
    start = datetime(2026, 10, 12, 20, 0, tzinfo=UTC)
    infos = _fill(store, start, 5, bad=(1, 2))
    archive = LocalArchive(store)
    night = archive.nights()[0]
    assert (night["frames"], night["bad"]) == (5, 2)
    assert night["cover"] == infos[3].name  # the middle one of the good frames
    good = archive.frames(NIGHT)
    assert (good["total"], [f["name"] for f in good["frames"]]) == (
        3, [infos[i].name for i in (0, 3, 4)]
    )  # fmt: skip
    assert archive.frames(NIGHT, bad=True)["total"] == 5
    loop = archive.loop(start + timedelta(minutes=45))
    assert [f["name"] for f in loop] == [infos[i].name for i in (0, 3, 4)]


def test_jump_to_a_time(tmp_path: Path) -> None:
    """The night view jumps to "HH:MM" camera time; before noon is the next morning."""
    store = ImageStore(tmp_path, TZ)
    # 20:00 UTC = 22:00 Vienna, every 10 min for 8 hours: 22:00 .. 05:10 local.
    infos = _fill(store, datetime(2026, 10, 12, 20, 0, tzinfo=UTC), 44)
    archive = LocalArchive(store)
    page = archive.frames(NIGHT, limit=3, at="23:05")
    assert page["offset"] == 7  # 23:10 is the first from 23:05 on
    assert page["frames"][0]["name"] == infos[7].name
    assert archive.frames(NIGHT, at="03:00")["offset"] == 30  # next morning
    assert archive.frames(NIGHT, at="11:59")["offset"] == 43  # after the last: the last
    assert archive.frames(NIGHT, at="12:00")["offset"] == 0  # noon starts the night
    assert archive.frames(NIGHT, at="25:00")["offset"] == 0  # nonsense: from the start
    assert archive.frames(NIGHT, offset=5)["offset"] == 5


def test_events_name_their_frame(tmp_path: Path) -> None:
    """The events list names the frame a detection was seen on, for "whole frame"."""
    from allskyhub_protocol import Event, EventKind

    store = ImageStore(tmp_path, TZ)
    start = datetime(2026, 10, 12, 20, 0, tzinfo=UTC)
    infos = _fill(store, start, 3)
    events = EventStore(store)
    at = infos[1].captured_at
    later = at + timedelta(minutes=35)
    for eid, when in (("meteor-20261012T201000Z", at), ("meteor-20261012T204500Z", later)):
        events._append(  # pyright: ignore[reportPrivateUsage]
            Event(
                id=eid, night_id=NIGHT, kind=EventKind.METEOR, start=when, end=when, confidence=0.9
            )
        )
    listed = LocalArchive(store, events).events(NIGHT)
    assert [e["frame"] for e in listed] == [infos[1].name, None]


def test_labels(tmp_path: Path) -> None:
    """SPEC §6.4 Labels: set in the UI, newer wins, false positives count nowhere."""
    from allskyhub_agent.detect.labels import Labels
    from allskyhub_protocol import Event, EventKind, EventLabel

    store = ImageStore(tmp_path, TZ)
    start = datetime(2026, 10, 12, 20, 0, tzinfo=UTC)
    _fill(store, start, 2)
    events = EventStore(store)
    for eid in ("meteor-20261012T200000Z", "meteor-20261012T201000Z"):
        at = datetime.strptime(eid[7:], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        events._append(  # pyright: ignore[reportPrivateUsage]
            Event(id=eid, night_id=NIGHT, kind=EventKind.METEOR, start=at, end=at, confidence=0.9)
        )
    labels = Labels(store)
    sent: list[EventLabel] = []
    archive = LocalArchive(store, events, labels, on_label=sent.append)
    assert archive.nights()[0]["events"] == 2
    lab = archive.set_label(NIGHT, "meteor-20261012T200000Z",
                            {"label": "false_positive", "reason": "satellite"})  # fmt: skip
    assert lab is not None
    assert sent == [lab]
    assert archive.nights()[0]["events"] == 1
    listed = {e["id"]: e for e in archive.events(NIGHT)}
    assert listed["meteor-20261012T200000Z"]["reason"] == "satellite"
    assert listed["meteor-20261012T201000Z"]["label"] is None
    assert archive.set_label(NIGHT, "meteor-20261012T235900Z", {"label": "confirmed"}) is None
    with pytest.raises(ValueError):  # noqa: PT011 - pydantic's ValidationError
        archive.set_label(NIGHT, "meteor-20261012T201000Z", {"label": "confirmed", "reason": "x"})
    # An older label from the hub does not win over the newer one set here.
    old = lab.model_copy(update={"label": "confirmed", "reason": None,
                                 "at": lab.at - timedelta(hours=1)})  # fmt: skip
    assert not labels.set(old)
    assert labels.false_positives(NIGHT) == {"meteor-20261012T200000Z"}
    assert len(labels.all()) == 1


def test_auto_label_gives_way(tmp_path: Path) -> None:
    """SPEC §6.4 Labels, §7.8: an aircraft match never replaces a label; the owner's wins."""
    from allskyhub_agent.detect.labels import Labels
    from allskyhub_protocol import EventLabel

    store = ImageStore(tmp_path, TZ)
    labels = Labels(store)
    eid = "meteor-20261012T200000Z"
    t = datetime(2026, 10, 12, 20, 5, tzinfo=UTC)
    auto = EventLabel(night_id=NIGHT, event_id=eid, label="false_positive", reason="aircraft",
                      auto=True, detail="DLH4AB 3c6444", at=t)  # fmt: skip
    assert labels.set(auto)
    assert not labels.set(auto.model_copy(update={"at": t - timedelta(minutes=1)}))
    assert labels.set(auto.model_copy(update={"at": t + timedelta(minutes=1)}))
    owner = EventLabel(night_id=NIGHT, event_id=eid, label="confirmed", at=t - timedelta(hours=1))
    assert labels.set(owner)  # older, but the owner's
    assert labels.night(NIGHT)[eid] == owner
    assert not labels.set(auto.model_copy(update={"at": t + timedelta(hours=2)}))
