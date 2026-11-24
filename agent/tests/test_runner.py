"""Capture loop end to end with the simulated camera (SPEC §4.1-§4.3)."""

import json
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
from allskyhub_agent.live import LiveState
from allskyhub_agent.process.livestack import LiveStack
from allskyhub_agent.process.moonhdr import MoonHdrTest
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
    moon_hdr: MoonHdrTest | None = None,
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
        moon_hdr=moon_hdr,
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


def test_moon_hdr_test_keeps_sets_under_a_bright_moon(tmp_path: Path) -> None:
    """Experimental (SPEC §4.13 draft): every 10 min at night with a bright moon, the long
    frame and four short ones at the lowest gain; the stored frames stay as they are."""
    hdr = MoonHdrTest(tmp_path / "hdr-test")
    runner, clock = make_runner(tmp_path, datetime(2026, 10, 25, 22, 0, tzinfo=UTC), moon_hdr=hdr)
    for _ in range(25):  # 30 s apart: 12.5 min
        runner.step()
        clock.sleep(runner.delay())
    sets = sorted((tmp_path / "hdr-test").glob("*/*"))
    taken = [datetime.strptime(p.name + "+0000", "%Y%m%d-%H%M%S%z") for p in sets]
    assert len(taken) >= 2
    assert all(b - a >= timedelta(minutes=10) for a, b in pairwise(taken))
    files = sorted(p.name for p in sets[0].iterdir())
    assert files == [
        "long.png", "set.json", "short-100000us.png", "short-10000us.png", "short-1000us.png",
        "short-500000us.png",
    ]  # fmt: skip
    meta = json.loads((sets[0] / "set.json").read_text())
    assert [s["exposure_us"] for s in meta["short"]] == [1000, 10_000, 100_000, 500_000]
    assert meta["long"]["exposure_us"] > 500_000
    assert meta["moon"]["altitude_deg"] > 0
    assert "lat" not in json.dumps(meta)  # the place is private
    hdr = MoonHdrTest(tmp_path / "new" / "hdr-test")
    runner, _ = make_runner(
        tmp_path / "new", datetime(2026, 10, 10, 22, 0, tzinfo=UTC), moon_hdr=hdr
    )
    runner.step()
    assert not (tmp_path / "new" / "hdr-test").exists()


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


def test_live_stack_feeds_only_the_live_image(tmp_path: Path) -> None:
    """SPEC §4.12: short frames close together are averaged for the live image; focus
    mode shows each frame as it is."""
    runner, clock = make_runner(tmp_path, datetime(2026, 10, 6, 10, 0, tzinfo=UTC))
    live = LiveState()
    stack = LiveStack(width=1280)
    runner._live = live  # pyright: ignore[reportPrivateUsage]
    runner._stack = stack  # pyright: ignore[reportPrivateUsage]
    first = runner.step()
    clock.sleep(2.0)
    runner.step()
    assert stack.size == 2
    store = ImageStore(tmp_path, ZoneInfo("Europe/Vienna"))
    assert len(store.read_index(first.night_id)) == 2  # stored frames stay single
    live.set_focus_mode(True)
    runner.step()
    assert stack.size == 0


class _WbCamera(SimCamera):
    """A simulated camera whose white balance can be set (like ZWO)."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.wb: list[tuple[float, float]] = []

    def set_white_balance(self, red: float, blue: float) -> None:
        self.wb.append((red, blue))


def test_night_white_balance_only_at_night(tmp_path: Path) -> None:
    """SPEC §4.11: the learned gains at night, the normal ones by day."""
    from allskyhub_agent.process.whitebalance import Gains, NightWhiteBalance

    start = datetime(2026, 10, 6, 10, 0, tzinfo=UTC)
    clock = SimClock(start)
    profile = get_profile("sim")
    cam = _WbCamera(lambda: 1.0, width=160, height=90, clock=clock)
    wb = NightWhiteBalance(tmp_path / "wb.json")
    wb._gains = Gains(0.8, 1.2)  # pyright: ignore[reportPrivateUsage]
    runner = Runner(
        camera=cam,
        auto_exposure=AutoExposure(profile.exposure),
        store=ImageStore(tmp_path, ZoneInfo("Europe/Vienna")),
        clock=clock,
        location=LOC,
        profile=profile.id,
        mask_radius_frac=profile.image_circle_frac,
        white_balance=wb,
    )
    runner.step()
    assert cam.wb[-1] == (1.0, 1.0)
    clock.sleep(12 * 3600.0)  # 22:00 UTC: night
    runner.step()
    assert cam.wb[-1][0] < 1.0
