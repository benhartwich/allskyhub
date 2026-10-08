"""Image storage (SPEC §4.5)."""

from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from allskyhub_agent.store.images import ImageStore, night_id

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
