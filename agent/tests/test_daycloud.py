"""Cloud cover by day from the sky's saturation (SPEC §6.3 `cloud_cover`, roadmap L22)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pytest

from allskyhub_agent.calib.solve import Solution
from allskyhub_agent.core.metering import circle_mask
from allskyhub_agent.detect.daycloud import (
    DayCloud,
    DayCloudConfig,
    SaturationHistory,
    saturation,
    sun_vector,
)
from allskyhub_agent.detect.sky import Mask, Rgb, SkyMeter
from allskyhub_protocol import FrameInfo, Mode

H, W = 720, 1280
SOL = Solution(cx=640.0, cy=360.0, a1=330.0, a3=0.0, rot=20.0, flip=-1.0, tilt_east=0.0,
               tilt_north=0.0, stars=30, rms_px=1.0, rms_deg=0.1, width=W, height=H)  # fmt: skip
LAT, LON = 48.2, 16.4
NOON = datetime(2026, 6, 21, 11, 0, tzinfo=UTC)  # the sun about 65° high
SKY = circle_mask(H, W, 0.48)
BLUE = (60, 120, 230)  # clear: saturation 0.74
GREY = (180, 180, 186)  # overcast: saturation 0.03


def frame(at: datetime = NOON, sun: float = 40.0, profile: str = "sim") -> FrameInfo:
    return FrameInfo(
        captured_at=at,
        night_id=(at - timedelta(hours=12)).strftime("%Y%m%d"),
        name=at.strftime("image-%Y%m%d%H%M%S.jpg"),
        mode=Mode.DAY if sun > -6 else Mode.NIGHT,
        exposure_us=1000,
        gain=0.0,
        mean=0.4,
        sun_elevation=sun,
        profile=profile,
    )


def flat(rgb: tuple[int, int, int]) -> Rgb:
    img = np.empty((H, W, 3), dtype=np.uint8)
    img[:, :] = rgb
    return img


def sun_pixel(at: datetime) -> tuple[int, int]:
    v = sun_vector(at, LAT, LON)
    alt = np.degrees(np.arcsin(np.array([float(v[2])])))
    az = np.degrees(np.arctan2(np.array([float(v[0])]), np.array([float(v[1])]))) % 360.0
    x, y = SOL.project(alt, az)
    return round(float(x[0])), round(float(y[0]))


def disc(img: Rgb, centre: tuple[int, int], r: int, rgb: tuple[int, int, int]) -> Rgb:
    yy, xx = np.mgrid[0:H, 0:W]
    img[(xx - centre[0]) ** 2 + (yy - centre[1]) ** 2 <= r * r] = rgb
    return img


def value(day: DayCloud, img: Rgb, at: datetime = NOON) -> float | None:
    return day.frame_value(frame(at), img, SKY, SOL, LAT, LON)


def test_saturation() -> None:
    pix = np.array([[230, 120, 60], [180, 180, 180], [0, 0, 0], [200, 100, 100]], np.uint8)
    assert saturation(pix).tolist() == pytest.approx([170 / 230, 0.0, 0.0, 0.5])


def test_clear_is_low_overcast_is_high() -> None:
    """SPEC §6.3: the share of sky pixels with a saturation below T (0.35 at first)."""
    day = DayCloud()
    assert value(day, flat(BLUE)) == 0.0
    assert value(day, flat(GREY)) == 1.0
    # Half the sky grey: the cover is about a half.
    half = flat(BLUE)
    half[:, : W // 2] = GREY
    v = value(day, half)
    assert v is not None
    assert 0.3 < v < 0.7


def test_glare_gives_null() -> None:
    """SPEC §6.3: null when more than 20 % of the used pixels are clipped."""
    day = DayCloud()
    burnt = flat(BLUE)
    burnt[: H // 2] = (255, 255, 252)  # half the sky clipped
    assert value(day, burnt) is None
    # A few clipped pixels are no glare.
    some = flat(BLUE)
    some[: H // 20] = (255, 255, 255)
    assert value(day, some) == 0.0
    assert day.measure(frame(), burnt, SKY, SOL, LAT, LON) is None


def test_the_sun_is_left_out() -> None:
    """SPEC §6.3: pixels within 25° of the sun do not count (the white glow around it)."""
    x, y = sun_pixel(NOON)
    glow = disc(flat(BLUE), (x, y), int(SOL.a1 / 90.0 * 20.0), (235, 235, 240))  # 20° radius
    assert value(DayCloud(), glow) == 0.0
    no_exclusion = DayCloud(cfg=DayCloudConfig(sun_deg=0.0))
    v = value(no_exclusion, glow)
    assert v is not None
    assert v > 0.05


def test_low_sky_is_left_out() -> None:
    """SPEC §6.3: only pixels at least 15° above the horizon."""
    r15 = int(SOL.a1 * 75.0 / 90.0) + 2  # the 15° circle in pixels
    low = flat(GREY)
    disc(low, (640, 360), r15, BLUE)
    assert value(DayCloud(), low) == 0.0


def test_mono_frames_and_no_sky_give_null() -> None:
    day = DayCloud()
    assert day.frame_value(frame(), np.zeros((H, W), np.uint8), SKY, SOL, LAT, LON) is None
    none: Mask = np.zeros((H, W), dtype=np.bool_)
    assert day.frame_value(frame(), flat(GREY), none, SOL, LAT, LON) is None


def test_median_of_the_last_15_minutes() -> None:
    """SPEC §6.3: each value is the median of the day values of the last 15 minutes."""
    day = DayCloud()

    def at(minutes: int, img: Rgb) -> float | None:
        return day.measure(frame(NOON + timedelta(minutes=minutes)), img, SKY, SOL, LAT, LON)

    assert at(0, flat(GREY)) == 1.0
    assert at(5, flat(GREY)) == 1.0
    assert at(10, flat(BLUE)) == 1.0  # median of 1, 1, 0
    assert at(12, flat(BLUE)) == 0.5  # median of 1, 1, 0, 0
    assert at(20, flat(BLUE)) == 0.0  # 0 and 5 have left the window: 0, 0, 0


def _sky_meter(day: DayCloud, sol: Solution | None) -> SkyMeter:
    """As the agent wires it (cli.py): only with an orientation."""
    meter = SkyMeter()

    def day_clouds(f: FrameInfo, rgb: Rgb, sky: Mask) -> float | None:
        return None if sol is None else day.measure(f, rgb, sky, sol, LAT, LON)

    meter.set_day_clouds(day_clouds)
    return meter


def test_sky_meter_by_day_at_night_and_without_orientation() -> None:
    """SPEC §6.3: by day (sun ≥ 3°) only and only with an orientation, else null."""
    overcast = flat(GREY)
    assert _sky_meter(DayCloud(), SOL).measure(frame(sun=40.0), overcast).cloud_cover == 1.0
    assert _sky_meter(DayCloud(), None).measure(frame(sun=40.0), overcast).cloud_cover is None
    assert _sky_meter(DayCloud(), SOL).measure(frame(sun=2.0), overcast).cloud_cover is None
    night = _sky_meter(DayCloud(), SOL).measure(frame(sun=-30.0), np.zeros((H, W, 3), np.uint8))
    assert night.cloud_cover is not None  # from the stars, as before: none seen
    assert SkyMeter().measure(frame(sun=40.0), overcast).cloud_cover is None


def test_threshold_learns_after_ten_days(tmp_path: Path) -> None:
    """SPEC §6.3: T = (p5 + p95 of the frame medians) / 2 after 10 days, else 0.35."""
    path = tmp_path / "calibration" / "day_saturation.json"
    hist = SaturationHistory(path, DayCloudConfig())
    start = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    for d in range(10):
        if d == 9:
            assert hist.threshold("sim") == 0.35
        for i in range(20):
            at = start + timedelta(days=d, minutes=15 * i)
            hist.add(at, LON, 0.70 if i % 2 else 0.50, "sim")  # clear 0.70, overcast 0.50
    assert hist.threshold("sim") == pytest.approx(0.60, abs=0.01)
    assert hist.threshold("other camera") == 0.35
    hist.save()
    again = SaturationHistory(path, DayCloudConfig())  # survives a restart
    assert again.days() == 10
    assert again.threshold("sim") == pytest.approx(0.60, abs=0.01)
    # 30 days later the old days are gone and the default is back.
    again.add(start + timedelta(days=45), LON, 0.6, "sim")
    assert again.days() == 1
    assert again.threshold("sim") == 0.35
    # A frame with a wrong clock neither wipes the history nor stays in it.
    again.add(datetime(1970, 1, 1, 12, tzinfo=UTC), LON, 0.6, "sim")
    assert again.days() == 1
    # Another camera starts again.
    again.add(start + timedelta(days=45, hours=1), LON, 0.6, "zwo")
    assert again.days() == 1


def test_frames_feed_the_threshold(tmp_path: Path) -> None:
    """The learned T is used: a pale blue sky reads clear once the camera's clear sky is pale."""
    pale = (150, 165, 190)  # saturation 0.21: below the default T
    day = DayCloud(tmp_path / "day_saturation.json")
    assert value(day, flat(pale)) == 1.0
    for d in range(10):
        for i, img in enumerate((flat(pale), flat(GREY)) * 3):
            at = NOON + timedelta(days=d, minutes=20 * i)
            day.frame_value(frame(at), img, SKY, SOL, LAT, LON)
    t = day.history.threshold("sim")
    assert 0.08 < t < 0.15  # midway between grey (0.03) and pale (0.21)
    assert value(day, flat(pale), NOON + timedelta(days=10)) == 0.0


def test_history_file_is_small(tmp_path: Path) -> None:
    path = tmp_path / "day_saturation.json"
    hist = SaturationHistory(path, DayCloudConfig())
    rng = np.random.default_rng(1)
    medians: npt.NDArray[np.float64] = rng.uniform(0.2, 0.5, 30 * 200)
    for i, m in enumerate(medians.tolist()):
        hist.add(datetime(2026, 9, 1, 8, tzinfo=UTC) + timedelta(minutes=4 * i), LON, m, "sim")
    hist.save()
    assert path.stat().st_size < 40_000
    assert hist.days() <= 30
