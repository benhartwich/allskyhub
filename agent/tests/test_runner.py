"""Capture loop end to end with the simulated camera (SPEC §4.1-§4.3)."""

from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from allskyhub_agent.adapters.sim import SimCamera, sky_radiance
from allskyhub_agent.core.clock import SimClock
from allskyhub_agent.core.exposure import AutoExposure
from allskyhub_agent.core.sun import sun_elevation
from allskyhub_agent.profiles import get_profile
from allskyhub_agent.runner import Location, LoopConfig, Runner
from allskyhub_agent.store.images import ImageStore
from allskyhub_protocol import FrameInfo, Mode

LOC = Location(48.14, 14.39)


def make_runner(tmp_path: Path, start: datetime) -> tuple[Runner, SimClock]:
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
