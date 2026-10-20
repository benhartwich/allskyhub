"""Image storage (SPEC §4.5)."""

import os
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from allskyhub_agent.store.images import ImageStore, night_id
from allskyhub_protocol import FrameInfo, Mode

TZ = ZoneInfo("Europe/Vienna")


def test_night_id_spans_midnight() -> None:
    evening = datetime(2026, 10, 6, 20, 0, tzinfo=UTC)  # 22:00 local
    morning = datetime(2026, 10, 7, 3, 0, tzinfo=UTC)  # 05:00 local
    noon = datetime(2026, 10, 7, 10, 0, tzinfo=UTC)  # 12:00 local
    assert night_id(evening, TZ) == "20261006"
    assert night_id(morning, TZ) == "20261006"
    assert night_id(noon, TZ) == "20261007"


def test_save_writes_image_and_thumbnail(tmp_path: Path) -> None:
    store = ImageStore(tmp_path, TZ)
    img = np.zeros((360, 640, 3), dtype=np.uint8)
    s = store.save(img, datetime(2026, 10, 6, 20, 0, 5, tzinfo=UTC))
    assert s.path == tmp_path / "images" / "20261006" / "image-20261006220005.jpg"
    assert s.path.is_file()
    assert s.thumbnail.is_file()
    assert not list(s.path.parent.glob("*.tmp"))


def test_cleanup_removes_old_nights_only(tmp_path: Path) -> None:
    store = ImageStore(tmp_path, TZ)
    for nid in ("20260920", "20260925", "20261005", "20261006"):
        (tmp_path / "images" / nid).mkdir(parents=True)
    (tmp_path / "images" / "keep-me").mkdir()
    removed = store.cleanup(datetime(2026, 10, 6, 20, 0, tzinfo=UTC), keep_days=10)
    assert removed == ["20260920", "20260925"]
    assert sorted(p.name for p in (tmp_path / "images").iterdir()) == [
        "20261005",
        "20261006",
        "keep-me",
    ]


def test_cleanup_keeps_products_longer_than_frames(tmp_path: Path) -> None:
    """SPEC §4.5: after keep_days only the frames go; after keep_product_days the night."""
    store = ImageStore(tmp_path, TZ)
    img = np.zeros((360, 640, 3), dtype=np.uint8)
    kept = ("timelapse.mp4", "keogram.jpg", "products.json", "sky.jsonl", "events.jsonl")
    for day in (10, 20, 30):
        at = datetime(2026, 9, day, 20, 0, tzinfo=UTC)
        s = store.save(img, at)
        store.append_index(FrameInfo(night_id=s.night_id, name=s.path.name, captured_at=at,
                                     mode=Mode.NIGHT, exposure_us=1, gain=0, mean=0.1,
                                     sun_elevation=-20, profile="sim"))  # fmt: skip
        folder = store.night_dir(s.night_id)
        for name in kept:
            (folder / name).write_bytes(b"x")
        (folder / "thumbnails" / "keogram.jpg").write_bytes(b"x")
        (folder / "events").mkdir()
    removed = store.cleanup(datetime(2026, 10, 6, 20, 0, tzinfo=UTC), 10, 20)
    assert removed == ["20260910"]
    for night in ("20260920", "20260930"):
        folder = store.night_dir(night)
        names = sorted(p.name for p in folder.iterdir())
        thumbs = sorted(p.name for p in (folder / "thumbnails").iterdir())
        if night == "20260920":  # older than 10 days: the frames are gone
            assert names == sorted([*kept, "events", "thumbnails"])
            assert thumbs == ["keogram.jpg"]
        else:
            assert "frames.jsonl" in names
            assert len(thumbs) == 2
    # Again: nothing more to do, and keep_days alone still works as before.
    assert store.cleanup(datetime(2026, 10, 6, 20, 0, tzinfo=UTC), 10, 20) == []
    assert store.cleanup(datetime(2026, 10, 6, 20, 0, tzinfo=UTC), 10) == ["20260920"]


def test_ensure_free_removes_oldest_nights_but_never_the_current(tmp_path: Path) -> None:
    store = ImageStore(tmp_path, TZ)
    for nid in ("20261001", "20261002", "20261003", "20261006"):
        (tmp_path / "images" / nid).mkdir(parents=True)
    state = {"free": 5}  # percent free; each removed night frees 3 %

    def usage(_: Path) -> tuple[int, int]:
        left = len(list((tmp_path / "images").iterdir()))
        return 100, state["free"] + 3 * (4 - left)

    removed = store.ensure_free(datetime(2026, 10, 6, 20, 0, tzinfo=UTC), 10.0, usage)
    assert removed == ["20261001", "20261002"]  # 5 % -> 8 % -> 11 %
    state["free"] = 0
    removed = store.ensure_free(datetime(2026, 10, 6, 20, 0, tzinfo=UTC), 50.0, usage)
    assert removed == ["20261003"]  # stops at the current night
    assert (tmp_path / "images" / "20261006").is_dir()


def test_a_night_that_cannot_be_removed_does_not_stop_the_cleanup(tmp_path: Path) -> None:
    # An import run as root left a folder the agent may not empty (2026-10-20 on the test Pi).
    if os.geteuid() == 0:
        pytest.skip("root may remove anything")
    store = ImageStore(tmp_path, TZ)
    for nid in ("20260901", "20260902", "20260903", "20261006"):
        (tmp_path / "images" / nid).mkdir(parents=True)
    locked = tmp_path / "images" / "20260901" / "events"
    locked.mkdir()
    (locked / "meteor.jpg").write_bytes(b"x")
    locked.chmod(0o555)
    try:
        removed = store.cleanup(datetime(2026, 10, 6, 20, 0, tzinfo=UTC), keep_days=10)
        assert removed == ["20260902", "20260903"]
        assert (tmp_path / "images" / "20260901").is_dir()
    finally:
        locked.chmod(0o755)
