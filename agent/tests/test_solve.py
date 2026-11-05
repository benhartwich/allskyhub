"""Plate solve and orientation (SPEC §4.8) on a synthetic sky."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import cv2
import numpy as np
import pytest

from allskyhub_agent.calib import orientation as orientation_mod
from allskyhub_agent.calib.orientation import OrientationConfig, Orienter
from allskyhub_protocol import EventValue, FrameInfo, Mode, SkyMetrics
from allskyhub_solve.solve import Solution

H, W = 1080, 1920
AT = datetime(2026, 10, 7, 21, 1, tzinfo=UTC)
LAT, LON = 50.036, 10.389
TRUE = [985.0, 532.0, 1005.0, -130.0, 18.2, 0.0, 0.0]  # like Benjamin's lens at half size
FLIP = -1.0


def frame(i: int, sun: float = -30.0) -> FrameInfo:
    t = AT + timedelta(minutes=10 * i)
    return FrameInfo(captured_at=t, night_id="20261007", name=f"f{i}.jpg", mode=Mode.NIGHT,
                     exposure_us=60_000_000, gain=100, mean=0.1, sun_elevation=sun,
                     profile="sim")  # fmt: skip


def sky(cloud: float, stars: int = 500) -> SkyMetrics:
    return SkyMetrics(at=AT, night_id="20261007", cloud_cover=cloud, sqm_mag=20.0, stars=stars)


SOL = Solution(cx=1.0, cy=2.0, a1=1000.0, a3=-100.0, rot=18.2, flip=-1.0, tilt_east=0.0,
               tilt_north=0.0, stars=30, rms_deg=0.3, rms_px=3.0)  # fmt: skip


def test_orienter_waits_for_a_clear_pair_and_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str]] = []

    def fake_solve(gray: np.ndarray, at: datetime, lat: float, lon: float,
                   static: np.ndarray | None = None) -> Solution:  # fmt: skip
        calls.append((str(at), str(static is not None)))
        return SOL

    monkeypatch.setattr(orientation_mod, "solve", fake_solve)
    img = tmp_path / "f.jpg"
    cv2.imwrite(str(img), np.zeros((10, 10), np.uint8))
    o = Orienter(tmp_path / "cal" / "orientation.json", location=lambda: (LAT, LON))
    assert o.latest is None
    assert not o.consider(frame(0), img, sky(0.5))  # cloudy
    assert not o.consider(frame(0, sun=-10), img, sky(0.0))  # not dark enough
    assert not o.consider(frame(0), img, sky(0.0))  # clear, but no earlier clear frame yet
    assert not o.consider(frame(1), img, sky(0.0))  # only 10 min apart
    assert o.consider(frame(2), img, sky(0.0))  # 20 min: solve
    o.join(5)
    latest = o.latest
    assert latest is not None
    assert latest.north_deg == 18.2
    assert latest.mirrored
    assert latest.stars == 30
    assert latest.lens is None  # no frame size kept: the lens cannot be drawn
    # Mid-exposure time.
    assert calls == [(str(frame(2).captured_at + timedelta(seconds=30)), "True")]
    assert not o.consider(frame(3), img, sky(0.0))  # solved: not again for a week
    again = Orienter(tmp_path / "cal" / "orientation.json", location=lambda: (LAT, LON))
    assert again.latest == latest  # after a restart


def test_orienter_gives_up_after_tries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_solution(*_: object, **__: object) -> None:
        return None

    monkeypatch.setattr(orientation_mod, "solve", no_solution)
    img = tmp_path / "f.jpg"
    cv2.imwrite(str(img), np.zeros((10, 10), np.uint8))
    o = Orienter(tmp_path / "o.json", location=lambda: (LAT, LON),
                 cfg=OrientationConfig(retry_s=0.0, max_tries=2))  # fmt: skip
    started = 0
    for i in range(10):
        if o.consider(frame(i), img, sky(0.0)):
            started += 1
        o.join(5)
    assert started == 2
    assert o.latest is None


def test_orientation_reports_the_lens(tmp_path: Path) -> None:
    # SPEC §4.8, §6.3: status carries the fitted lens when the frame size is known.
    sol = replace(SOL, tilt_east=1.5, tilt_north=-0.5, width=1920, height=1080)
    o = Orienter(tmp_path / "orientation.json", location=lambda: (LAT, LON))
    o.store(sol, datetime(2026, 10, 12, 22, 0, tzinfo=UTC))
    latest = o.latest
    assert latest is not None
    assert latest.lens is not None
    assert (latest.lens.cx, latest.lens.a1, latest.lens.a3) == (1.0, 1000.0, -100.0)
    assert (latest.lens.tilt_east_deg, latest.lens.tilt_north_deg) == (1.5, -0.5)
    assert (latest.lens.width, latest.lens.height) == (1920, 1080)


def test_meteor_event_gets_its_sky_position(tmp_path: Path) -> None:
    from zoneinfo import ZoneInfo

    from allskyhub_agent.detect.events import EventStore
    from allskyhub_agent.detect.meteor import MeteorConfig, MeteorHit, find_streaks
    from allskyhub_agent.store.images import ImageStore

    store = ImageStore(tmp_path, ZoneInfo("UTC"))
    sol = Solution(cx=W / 2, cy=H / 2, a1=500.0, a3=0.0, rot=0.0, flip=1.0, tilt_east=0.0,
                   tilt_north=0.0, stars=30, rms_px=1.0, rms_deg=0.1,
                   width=W, height=H)  # fmt: skip
    events = EventStore(store, orientation=lambda: sol)
    gray = np.zeros((H, W), np.uint8)
    cv2.line(gray, (W // 2 + 200, H // 2 - 60), (W // 2 + 300, H // 2 + 60), 255, 2)
    rgb = np.empty((H, W, 3), dtype=np.uint8)
    rgb[:, :, :] = gray[:, :, None]
    stored = store.save(rgb, AT)
    f = frame(0).model_copy(update={"name": stored.name, "night_id": stored.night_id})
    s = find_streaks(gray, MeteorConfig())[0]
    ev = events.save_meteor(MeteorHit(f, store.night_dir(f.night_id) / f.name, s))
    # 250 px right of the centre with r = 500 t: zenith angle 45°, east (not mirrored).
    assert ev.data["azimuth_deg"] == pytest.approx(90, abs=2)
    assert ev.data["altitude_deg"] == pytest.approx(45, abs=2)
    # SPEC §6.4 (C4): the ends lie on either side of the midpoint, one higher, one lower.
    alts = sorted([_num(ev.data["start_altitude_deg"]), _num(ev.data["end_altitude_deg"])])
    mid = _num(ev.data["altitude_deg"])
    assert alts[0] < mid < alts[1]
    assert ev.data["start_azimuth_deg"] is not None


def _num(value: EventValue) -> float:
    assert isinstance(value, int | float)
    return float(value)
