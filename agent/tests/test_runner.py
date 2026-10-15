"""Capture loop end to end with the simulated camera (SPEC §4.1-§4.3)."""

from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from allskyhub_agent.adapters.sim import SimCamera, sky_radiance
from allskyhub_agent.core.clock import SimClock
from allskyhub_agent.core.exposure import AutoExposure
from allskyhub_agent.core.quality import FrameQuality
from allskyhub_agent.core.sun import sun_elevation
from allskyhub_agent.profiles import get_profile
from allskyhub_agent.runner import Location, LoopConfig, Runner
from allskyhub_agent.store.images import ImageStore
from allskyhub_protocol import FrameInfo, Mode

LOC = Location(50.04, 10.39)


def make_runner(
    tmp_path: Path,
    start: datetime,
    quality: FrameQuality | None = None,
    moon_mode: bool = False,
) -> tuple[Runner, SimClock]:
    clock = SimClock(start)
    profile = get_profile("sim")

    def radiance() -> float:
        return sky_radiance(sun_elevation(clock.now(), LOC.lat, LOC.lon))

    runner = Runner(
        camera=SimCamera(radiance, width=160, height=90, clock=clock),
        auto_exposure=AutoExposure(profile.exposure),
        store=ImageStore(tmp_path, ZoneInfo("Europe/Vienna")),
        clock=clock,
        location=LOC,
        profile=profile.id,
        loop=LoopConfig(day_delay_s=60.0, night_delay_s=30.0),
        mask_radius_frac=profile.image_circle_frac,
        quality=quality,
        moon_mode=moon_mode,
    )
    return runner, clock


def test_dusk_switches_to_night_once_and_keeps_brightness(tmp_path: Path) -> None:
    # 2026-10-06 16:30 UTC = 18:30 local, sun about +1°; run into the night.
    runner, clock = make_runner(tmp_path, datetime(2026, 10, 6, 16, 30, tzinfo=UTC))
    frames: list[FrameInfo] = []
    while clock.now() < datetime(2026, 10, 6, 19, 30, tzinfo=UTC):
        frames.append(runner.step())
        clock.sleep(runner.delay())

    modes = [f.mode for f in frames]
    assert modes[0] is Mode.DAY
    assert modes[-1] is Mode.NIGHT
    switches = sum(1 for a, b in pairwise(modes) if a is not b)
    assert switches == 1

    night = [f for f in frames if f.mode is Mode.NIGHT]
    # After settling, night frames sit near the target despite the darkening sky.
    settled = night[len(night) // 2 :]
    assert all(f.mean == pytest.approx(0.20, abs=0.08) for f in settled)
    assert frames[-1].exposure_us == 60_000_000
    assert frames[-1].gain > 0

    files = list((tmp_path / "images" / "20261006").glob("image-*.jpg"))
    assert len(files) == len(frames)


def test_run_reports_each_frame(tmp_path: Path) -> None:
    runner, _ = make_runner(tmp_path, datetime(2026, 10, 6, 11, 0, tzinfo=UTC))
    seen: list[FrameInfo] = []
    assert runner.run(5, on_frame=seen.append) == 5
    assert [f.mode for f in seen] == [Mode.DAY] * 5
    assert seen[-1].mean == pytest.approx(0.35, abs=0.05)


def test_bad_frames_are_marked_not_dropped(tmp_path: Path) -> None:
    """SPEC §4.9: a frame outside the limits is stored and marked."""
    q = FrameQuality(dark_max=0.02, bright_min=0.9)
    q_all_dark = FrameQuality(dark_max=0.99)
    assert (q.judge(0.01), q.judge(0.2), q.judge(0.95)) == ("dark", None, "bright")
    assert FrameQuality(enabled=False).judge(0.0) is None
    assert FrameQuality(dark_max=0.0, bright_min=1.0).judge(0.0) is None  # 0 % / 100 %: off

    # Every frame is "too dark":
    runner, _ = make_runner(tmp_path, datetime(2026, 10, 6, 20, 0, tzinfo=UTC), q_all_dark)
    f = runner.step()
    assert f.bad == "dark"
    stored = ImageStore(tmp_path, ZoneInfo("Europe/Vienna")).read_index(f.night_id)
    assert [(s.name, s.bad) for s in stored] == [(f.name, "dark")]
    assert (tmp_path / "images" / f.night_id / f.name).is_file()


def test_moon_mode_follows_the_moon(tmp_path: Path) -> None:
    """SPEC §4.3: full moon on 2026-10-26, up at midnight; new moon on 2026-10-10."""
    runner, clock = make_runner(tmp_path, datetime(2026, 10, 25, 22, 0, tzinfo=UTC), moon_mode=True)
    frames: list[FrameInfo] = []
    for _ in range(40):
        frames.append(runner.step())
        clock.sleep(runner.delay())
    assert runner.moon
    assert frames[-1].mean == pytest.approx(0.12, abs=0.05)
    runner, _ = make_runner(tmp_path, datetime(2026, 10, 10, 22, 0, tzinfo=UTC), moon_mode=True)
    runner.step()
    assert not runner.moon


def test_turn() -> None:
    """SPEC §4.1: flip first, then rotate clockwise."""
    from allskyhub_agent.core.transform import turn

    img = np.arange(6, dtype=np.uint8).reshape(2, 3)  # [[0 1 2] [3 4 5]]
    assert turn(img) is img
    assert turn(img, "horizontal").tolist() == [[2, 1, 0], [5, 4, 3]]
    assert turn(img, "vertical").tolist() == [[3, 4, 5], [0, 1, 2]]
    assert turn(img, rotate=90).tolist() == [[3, 0], [4, 1], [5, 2]]
    assert turn(img, rotate=180).tolist() == [[5, 4, 3], [2, 1, 0]]
    assert turn(img, rotate=270).tolist() == [[2, 5], [1, 4], [0, 3]]
    rgb = np.zeros((4, 6, 3), dtype=np.uint8)
    assert turn(rgb, rotate=90).shape == (6, 4, 3)


def test_day_capture_live_and_off(tmp_path: Path) -> None:
    """SPEC §4.1: `live` keeps only the newest day frame and indexes none; `off` takes none."""
    noon = datetime(2026, 10, 6, 10, 0, tzinfo=UTC)
    runner, clock = make_runner(tmp_path, noon)
    runner._day_capture = "live"  # pyright: ignore[reportPrivateUsage]
    infos = [runner.step() for _ in range(3)]
    assert all(f.mode is Mode.DAY for f in infos)
    store = ImageStore(tmp_path, ZoneInfo("Europe/Vienna"))
    night = infos[0].night_id
    assert store.read_index(night) == []
    files = sorted(p.name for p in (tmp_path / "images" / night).glob("image-*.jpg"))
    assert files == [infos[-1].name]
    off, clock = make_runner(tmp_path / "off", noon)
    off._day_capture = "off"  # pyright: ignore[reportPrivateUsage]
    # One hour of "off" by day takes nothing; the sun is checked every minute.
    sleeps: list[float] = []
    real_sleep = clock.sleep

    def sleep(s: float) -> None:
        sleeps.append(s)
        real_sleep(s)
        if clock.now() >= noon + timedelta(hours=1):
            off.stop()

    clock.sleep = sleep  # type: ignore[method-assign]
    taken: list[FrameInfo] = []
    off.run(on_frame=taken.append)
    assert taken == []
    assert set(sleeps) == {60.0}
