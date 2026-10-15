"""Frame index and night products (SPEC §5.1, §5.2)."""

from __future__ import annotations

import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pytest
from PIL import Image as PILImage

from allskyhub_agent.adapters.sim import SimCamera, sky_radiance
from allskyhub_agent.core.clock import SimClock
from allskyhub_agent.core.exposure import AutoExposure
from allskyhub_agent.core.sun import sun_elevation
from allskyhub_agent.products.build import (
    KEOGRAM,
    STARTRAILS,
    TIMELAPSE,
    ProductConfig,
    build_night,
    flash_frames,
    keogram,
    newest_products,
    night_products,
    startrails,
)
from allskyhub_agent.products.worker import DawnDetector, ProductWorker
from allskyhub_agent.profiles import get_profile
from allskyhub_agent.runner import Location, LoopConfig, Runner
from allskyhub_agent.store.images import INDEX_NAME, ImageStore
from allskyhub_protocol import FrameInfo, Mode

TZ = ZoneInfo("Europe/Vienna")
LOC = Location(48.31, 14.29)
HAVE_FFMPEG = shutil.which("ffmpeg") is not None


def _frame(night: str, name: str, mode: Mode, t: datetime, mean: float = 0.2) -> FrameInfo:
    return FrameInfo(
        captured_at=t,
        night_id=night,
        name=name,
        mode=mode,
        exposure_us=1000,
        gain=0,
        mean=mean,
        sun_elevation=-20 if mode is Mode.NIGHT else 10,
        profile="sim",
    )


def test_index_roundtrip_skips_damaged_lines(tmp_path: Path) -> None:
    store = ImageStore(tmp_path, TZ)
    t0 = datetime(2026, 10, 6, 20, 0, tzinfo=UTC)
    store.append_index(_frame("20261006", "b.jpg", Mode.NIGHT, t0 + timedelta(minutes=1)))
    with (tmp_path / "images" / "20261006" / INDEX_NAME).open("a") as f:
        f.write("{not json\n")
    store.append_index(_frame("20261006", "a.jpg", Mode.NIGHT, t0))
    assert [f.name for f in store.read_index("20261006")] == ["a.jpg", "b.jpg"]
    assert store.read_index("20200101") == []


def test_dawn_detector() -> None:
    d = DawnDetector()
    t = datetime(2026, 10, 7, 4, 0, tzinfo=UTC)
    assert d.feed(_frame("20261006", "1", Mode.DAY, t)) is None
    assert d.feed(_frame("20261006", "2", Mode.NIGHT, t)) is None
    assert d.feed(_frame("20261006", "3", Mode.NIGHT, t)) is None
    assert d.feed(_frame("20261006", "4", Mode.DAY, t)) == "20261006"
    assert d.feed(_frame("20261006", "5", Mode.DAY, t)) is None


def _write(path: Path, arr: np.ndarray) -> Path:
    PILImage.fromarray(arr.astype(np.uint8)).save(path, format="PNG")
    return path


def test_keogram_takes_the_centre_band(tmp_path: Path) -> None:
    paths: list[Path] = []
    for i in range(5):
        img = np.zeros((40, 30, 3), dtype=np.uint8)
        img[:, 14:17, :] = 50 * i  # centre band brightness grows per frame
        paths.append(_write(tmp_path / f"{i}.png", img))
    keo = keogram(paths, ProductConfig())
    assert keo is not None
    assert keo.shape == (40, 5, 3)
    assert keo[20, :, 0].tolist() == [0, 50, 100, 150, 200]


def test_startrails_is_max_of_dark_frames(tmp_path: Path) -> None:
    frames: list[tuple[Path, float]] = []
    for i in range(12):
        img = np.zeros((20, 20, 3), dtype=np.uint8)
        img[i, i, :] = 200  # one "star" moving along the diagonal
        frames.append((_write(tmp_path / f"{i}.png", img), 0.1))
    bright = np.full((20, 20, 3), 250, dtype=np.uint8)
    frames.append((_write(tmp_path / "bright.png", bright), 0.9))  # too bright, ignored
    st = startrails(frames, ProductConfig())
    assert st is not None
    assert all(st[i, i, 0] == 200 for i in range(12))
    assert st[0, 5, 0] == 0
    assert startrails(frames[:5], ProductConfig()) is None  # fewer than 10 dark frames


def _simulate_night(tmp_path: Path) -> tuple[ImageStore, list[FrameInfo]]:
    """Dusk to dawn of 2026-10-06/07 with the simulated camera, small frames."""
    clock = SimClock(datetime(2026, 10, 6, 16, 30, tzinfo=UTC))
    store = ImageStore(tmp_path, TZ)
    profile = get_profile("sim")

    def radiance() -> float:
        return sky_radiance(sun_elevation(clock.now(), LOC.lat, LOC.lon))

    runner = Runner(
        camera=SimCamera(radiance, width=96, height=64, clock=clock),
        auto_exposure=AutoExposure(profile.exposure),
        store=store,
        clock=clock,
        location=LOC,
        profile=profile.id,
        loop=LoopConfig(day_delay_s=600.0, night_delay_s=600.0),
        mask_radius_frac=profile.image_circle_frac,
        local_tz=TZ,
    )
    frames: list[FrameInfo] = []
    while clock.now() < datetime(2026, 10, 7, 6, 0, tzinfo=UTC):
        frames.append(runner.step())
        clock.sleep(runner.delay())
    return store, frames


def test_full_night_products(tmp_path: Path) -> None:
    store, frames = _simulate_night(tmp_path)
    night = [f for f in frames if f.mode is Mode.NIGHT]
    assert len(night) > 40

    result = build_night(store, "20261006")
    assert result.frames == len(night)
    folder = tmp_path / "images" / "20261006"

    assert KEOGRAM in result.built
    with PILImage.open(folder / KEOGRAM) as keo:
        assert keo.size == (len(night), 64)
    assert STARTRAILS in result.built
    assert (folder / "thumbnails" / KEOGRAM).is_file()
    assert (folder / "thumbnails" / STARTRAILS).is_file()
    assert not list(folder.glob("*.tmp*"))

    announced = night_products(store, "20261006")
    assert announced is not None
    names = {p.name: p for p in announced.products}
    assert names[KEOGRAM].thumbnail is True
    assert names[KEOGRAM].size == (folder / KEOGRAM).stat().st_size
    if HAVE_FFMPEG:
        assert names[TIMELAPSE].duration_s == pytest.approx(len(night) / 25, abs=0.1)
        assert newest_products(store) == announced

    if HAVE_FFMPEG:
        assert TIMELAPSE in result.built
        probe = subprocess.run(
            ["ffmpeg", "-hide_banner", "-i", str(folder / TIMELAPSE)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert "h264" in probe.stderr
        assert (folder / "thumbnails" / "timelapse.jpg").is_file()
    else:
        assert result.skipped[TIMELAPSE] == "ffmpeg not found"


def test_missing_ffmpeg_skips_only_the_timelapse(tmp_path: Path) -> None:
    store, _ = _simulate_night(tmp_path)
    result = build_night(store, "20261006", ProductConfig(ffmpeg="no-such-ffmpeg"))
    assert result.skipped == {TIMELAPSE: "ffmpeg not found"}
    assert set(result.built) == {KEOGRAM, STARTRAILS}


def test_night_without_frames(tmp_path: Path) -> None:
    result = build_night(ImageStore(tmp_path, TZ), "20261006")
    assert result.built == []
    assert set(result.skipped) == {KEOGRAM, STARTRAILS, TIMELAPSE}


def test_worker_builds_at_dawn_and_cleans_up(tmp_path: Path) -> None:
    store, frames = _simulate_night(tmp_path)
    (tmp_path / "images" / "20200101").mkdir()  # an old night that retention removes
    done: list[str] = []
    worker = ProductWorker(
        store, ProductConfig(ffmpeg="no-such-ffmpeg"), on_done=lambda r: done.append(r.night_id)
    )
    worker.start()
    for f in frames:
        worker.on_frame(f)
    worker.stop(timeout=60)
    assert done == ["20261006"]
    assert (tmp_path / "images" / "20261006" / KEOGRAM).is_file()
    assert not (tmp_path / "images" / "20200101").exists()


@pytest.mark.parametrize("bad", [b"", b"not an image"])
def test_unreadable_frames_are_skipped(tmp_path: Path, bad: bytes) -> None:
    good = np.zeros((10, 10, 3), dtype=np.uint8)
    paths = [_write(tmp_path / "a.png", good), tmp_path / "broken.jpg"]
    paths[1].write_bytes(bad)
    keo = keogram(paths, ProductConfig())
    assert keo is not None
    assert keo.shape[1] == 1


def test_products_switched_off(tmp_path: Path) -> None:
    # Roadmap L9: only the products the owner wants.
    store, _ = _simulate_night(tmp_path)
    result = build_night(store, "20261006", ProductConfig(keogram=False, timelapse=False))
    assert result.built == [STARTRAILS]
    assert result.skipped == {KEOGRAM: "switched off", TIMELAPSE: "switched off"}
    assert not (tmp_path / "images" / "20261006" / KEOGRAM).exists()


def test_bad_frames_stay_out_of_the_products(tmp_path: Path) -> None:
    """SPEC §4.9: bad frames are left out of keogram, startrails and timelapse."""
    store, frames = _simulate_night(tmp_path)
    night = [f for f in frames if f.mode is Mode.NIGHT]
    index = store.night_dir("20261006") / "frames.jsonl"
    lines = [
        f.model_copy(update={"bad": "bright"}) if i % 4 == 0 and f.mode is Mode.NIGHT else f
        for i, f in enumerate(frames)
        if f.night_id == "20261006"
    ]
    index.write_text("".join(f.model_dump_json() + "\n" for f in lines))
    bad = sum(1 for f in lines if f.bad)
    result = build_night(store, "20261006", ProductConfig(timelapse=False))
    assert bad > 5
    assert result.frames == len(night) - bad
    with PILImage.open(store.night_dir("20261006") / KEOGRAM) as keo:
        assert keo.size[0] == len(night) - bad


def test_flash_frames() -> None:
    """SPEC §5.2 (roadmap L32): a frame lit up for one exposure by a light near the camera,
    not a ramp at dusk, more gain on one frame or the first and last frame."""
    t0 = datetime(2026, 10, 14, 20, 0, tzinfo=UTC)

    def f(i: int, mean: float, gain: float = 100.0) -> FrameInfo:
        frame = _frame("20261014", f"f{i}.jpg", Mode.NIGHT, t0 + timedelta(minutes=i), mean)
        return frame.model_copy(update={"gain": gain, "exposure_us": 60_000_000})

    cfg = ProductConfig()
    # The lamp: +0.05 over both neighbours; the frame after has less gain (auto exposure).
    assert flash_frames([f(0, 0.20), f(1, 0.25), f(2, 0.19, 90.0), f(3, 0.20)], cfg) == {"f1.jpg"}
    # Dusk: brighter and brighter, never brighter than both neighbours.
    assert flash_frames([f(i, 0.10 + 0.05 * i) for i in range(6)], cfg) == set()
    # More gain on one frame: brighter as stored, the same light.
    assert flash_frames([f(0, 0.20), f(1, 0.25, 120.0), f(2, 0.20)], cfg) == set()
    # Too little to matter, and the first and last frames are kept.
    assert flash_frames([f(0, 0.20), f(1, 0.21), f(2, 0.20)], cfg) == set()
    assert flash_frames([f(0, 0.40), f(1, 0.20), f(2, 0.40)], cfg) == set()


def test_flash_frames_stay_out_of_the_startrails(tmp_path: Path) -> None:
    """SPEC §5.2 (roadmap L32): the lit frame is not in the startrails, the rest is."""
    store = ImageStore(tmp_path, TZ)
    t0 = datetime(2026, 10, 14, 20, 0, tzinfo=UTC)
    nights: set[str] = set()
    for i in range(12):
        lamp = i == 5
        rgb = np.full((40, 60, 3), 200 if lamp else 40, dtype=np.uint8)
        stored = store.save(rgb, t0 + timedelta(minutes=i))
        mean = 200 / 255 if lamp else 40 / 255
        frame = _frame(stored.night_id, stored.name, Mode.NIGHT, t0 + timedelta(minutes=i), mean)
        store.append_index(frame)
        nights.add(stored.night_id)
    (night,) = nights
    build_night(store, night, ProductConfig(timelapse=False, startrails_max_mean=0.9))
    with PILImage.open(store.night_dir(night) / STARTRAILS) as st:
        assert int(np.asarray(st).max()) < 100
