"""Day products, mini timelapse and keolapse (SPEC §5.2, §5.4, roadmap L21)."""

from __future__ import annotations

import io
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
from PIL import Image as PILImage

from allskyhub_agent.products.build import KEOGRAM, ProductConfig, night_products
from allskyhub_agent.products.day import (
    DAY_KEOGRAM,
    DAY_TIMELAPSE,
    DayConfig,
    DuskDetector,
    build_day,
    build_mini,
    day_frames,
    day_products,
)
from allskyhub_agent.products.keolapse import COLUMN, HEIGHT, keolapse, nights_between
from allskyhub_agent.store.images import ImageStore
from allskyhub_protocol import FrameInfo, Mode

TZ = ZoneInfo("Europe/Vienna")
HAVE_FFMPEG = shutil.which("ffmpeg") is not None


def _add(store: ImageStore, at: datetime, mode: Mode, bad: str | None = None) -> FrameInfo:
    img = np.full((64, 96, 3), 120 if mode is Mode.DAY else 20, dtype=np.uint8)
    saved = store.save(img, at)
    info = FrameInfo(
        captured_at=at, night_id=saved.night_id, name=saved.name, mode=mode,
        exposure_us=1000, gain=0.0, mean=0.4, sun_elevation=20.0, profile="sim",
        bad=bad,  # pyright: ignore[reportArgumentType]
    )  # fmt: skip
    store.append_index(info)
    return info


def _day(store: ImageStore) -> list[FrameInfo]:
    """13 October 2026 local: frames from 07:00 to 18:00 every 30 min, one bad."""
    out: list[FrameInfo] = []
    start = datetime(2026, 10, 13, 5, 0, tzinfo=UTC)  # 07:00 local
    for i in range(23):
        out.append(_add(store, start + timedelta(minutes=30 * i), Mode.DAY,
                        "bright" if i == 3 else None))  # fmt: skip
    # Night frames either side do not count.
    _add(store, datetime(2026, 10, 13, 2, 0, tzinfo=UTC), Mode.NIGHT)
    _add(store, datetime(2026, 10, 13, 17, 0, tzinfo=UTC), Mode.NIGHT)
    return out


def test_day_frames_span_two_folders(tmp_path: Path) -> None:
    store = ImageStore(tmp_path, TZ)
    frames = _day(store)
    got = day_frames(store, "20261013", TZ)
    assert {f.night_id for f in got} == {"20261012", "20261013"}  # morning, afternoon
    assert [f.name for f in got] == [f.name for f in frames if f.bad is None]


def test_build_day(tmp_path: Path) -> None:
    store = ImageStore(tmp_path, TZ)
    _day(store)
    result = build_day(store, "20261013", TZ, DayConfig(timelapse=HAVE_FFMPEG, keogram=True))
    assert result.frames == 22
    folder = store.night_dir("20261013")
    with PILImage.open(folder / DAY_KEOGRAM) as keo:
        assert keo.size == (22, 64)
    listed = {p["name"]: p for p in day_products(store, "20261013")}
    assert listed[DAY_KEOGRAM]["thumbnail"] is True
    if HAVE_FFMPEG:
        assert DAY_TIMELAPSE in result.built
        assert listed[DAY_TIMELAPSE]["duration_s"] == round(22 / 25, 1)
        assert listed[DAY_TIMELAPSE]["kind"] == "day_timelapse"
    assert day_products(store, "20261012") == []
    # SPEC §5.2/§6.3: announced to the hub with the night id's products.
    announced = night_products(store, "20261013")
    assert announced is not None
    kinds = {p.kind.value: p for p in announced.products}
    assert "day_keogram" in kinds
    if HAVE_FFMPEG:
        assert kinds["day_timelapse"].duration_s == round(22 / 25, 1)
    empty = build_day(store, "20261020", TZ)
    assert empty.frames == 0
    assert not empty.built


def test_dusk_detector() -> None:
    d = DuskDetector()
    at = datetime(2026, 10, 13, 16, 0, tzinfo=UTC)

    def f(mode: Mode) -> FrameInfo:
        return FrameInfo(
            captured_at=at, night_id="20261013", name="x.jpg", mode=mode,
            exposure_us=1, gain=0, mean=0.2, sun_elevation=0, profile="sim",
        )  # fmt: skip

    assert d.feed(f(Mode.DAY)) is None
    assert d.feed(f(Mode.NIGHT)) == "20261013"
    assert d.feed(f(Mode.NIGHT)) is None
    assert d.feed(f(Mode.DAY)) is None


def test_mini_takes_the_last_hour(tmp_path: Path) -> None:
    store = ImageStore(tmp_path, TZ)
    _day(store)
    out = tmp_path / "live" / "mini.mp4"
    now = datetime(2026, 10, 13, 16, 0, tzinfo=UTC)
    reason = build_mini(store, out, now, ProductConfig())
    if HAVE_FFMPEG:
        assert reason is None
        assert out.is_file()
    else:
        assert reason == "ffmpeg not found"
    assert build_mini(store, out, now + timedelta(days=3), ProductConfig()) is not None


def test_keolapse(tmp_path: Path) -> None:
    store = ImageStore(tmp_path, TZ)
    for night, shade in (("20261010", 50), ("20261011", 0), ("20261012", 200)):
        folder = store.night_dir(night)
        folder.mkdir(parents=True)
        if shade:
            PILImage.new("RGB", (30, 100), (shade,) * 3).save(folder / KEOGRAM)
    assert nights_between(store, "20261012", "20261010") == ["20261010", "20261011", "20261012"]
    assert nights_between(store, "bad", "20261012") == []
    cache = tmp_path / "cache"
    data = keolapse(store, "20261010", "20261012", cache)
    assert data is not None
    with PILImage.open(io.BytesIO(data)) as im:
        assert im.size == (3 * COLUMN, HEIGHT)
        px = np.asarray(im.convert("L"))
    assert abs(int(px[10, 5]) - 50) < 8
    assert abs(int(px[10, COLUMN + 5]) - 40) < 8  # no keogram: grey gap
    assert abs(int(px[10, 2 * COLUMN + 5]) - 200) < 8
    assert len(list(cache.glob("keolapse-*.jpg"))) == 1
    assert keolapse(store, "20261010", "20261012", cache) == data  # from the cache
    PILImage.new("RGB", (30, 100), (90,) * 3).save(store.night_dir("20261011") / KEOGRAM)
    assert keolapse(store, "20261010", "20261012", cache) != data
    assert len(list(cache.glob("keolapse-*.jpg"))) == 1  # the stale one is gone
    assert keolapse(store, "20250101", "20250102", cache) is None
