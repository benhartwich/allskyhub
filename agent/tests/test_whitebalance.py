"""Night white balance from the sky (SPEC §4.11)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from allskyhub_agent.adapters.camera import Image
from allskyhub_agent.process.whitebalance import (
    Gains,
    NightWhiteBalance,
    WhiteBalanceConfig,
    inner_sky,
    sky_ratios,
)
from allskyhub_agent.settings import AgentSettings

T0 = datetime(2026, 11, 15, 21, 0, tzinfo=UTC)
CFG = WhiteBalanceConfig()


def _sky(red: float, green: float, blue: float, stars: int = 40) -> Image:
    rng = np.random.default_rng(1)
    img = np.empty((90, 120, 3), dtype=np.uint8)
    for i, level in enumerate((red, green, blue)):
        noise = np.minimum(np.maximum(rng.normal(level, 3, (90, 120)), 0), 255)
        img[:, :, i] = noise.astype(np.uint8)
    ys, xs = rng.integers(0, 90, stars), rng.integers(0, 120, stars)
    img[ys, xs] = 255  # stars and clipped pixels do not count
    return img


ALL = np.ones((90, 120), dtype=np.bool_)


def test_sky_ratios_without_stars() -> None:
    ratios = sky_ratios(_sky(36, 30, 24), ALL, CFG)
    assert ratios is not None
    assert abs(ratios[0] - 1.2) < 0.03
    assert abs(ratios[1] - 0.8) < 0.03
    assert sky_ratios(_sky(2, 2, 2), ALL, CFG) is None  # too dark for a ratio in 8 bit
    assert sky_ratios(_sky(36, 30, 24), np.zeros((90, 120), dtype=np.bool_), CFG) is None


def test_inner_sky_keeps_the_centre() -> None:
    inner = inner_sky(ALL, 0.5)
    assert inner[45, 60]
    assert not inner[0, 0]
    assert 0 < int(inner.sum()) < int(ALL.sum())


def test_gains_move_slowly_toward_grey_and_are_kept(tmp_path: Path) -> None:
    path = tmp_path / "calibration" / "whitebalance.json"
    wb = NightWhiteBalance(path)
    reddish = _sky(36, 30, 24)
    g = wb.observe(T0, reddish, ALL, hold=False)
    assert 0.95 <= g.red < 1.0  # at most 5 % a step
    assert 1.0 < g.blue <= 1.05
    # Not again within 10 minutes.
    assert wb.observe(T0 + timedelta(minutes=5), reddish, ALL, hold=False) == g
    # Kept across a restart.
    assert NightWhiteBalance(path).gains == g


def test_hold_and_limits(tmp_path: Path) -> None:
    wb = NightWhiteBalance(tmp_path / "wb.json")
    green = _sky(20, 60, 20)  # an aurora would look like this
    assert wb.observe(T0, green, ALL, hold=True) == Gains()
    at, g = T0, Gains()
    for _ in range(100):  # however long the sky stays this colour: within ±40 %
        at += timedelta(minutes=10)
        g = wb.observe(at, green, ALL, hold=False)
    assert (g.red, g.blue) == (1.4, 1.4)


def test_owner_white_balance_keeps_night_wb_fixed(tmp_path: Path) -> None:
    """SPEC §4.11: `auto` is the default only without the owner's own white balance."""
    p = tmp_path / "settings.json"
    p.write_text('{"wb_r": 52}')
    assert AgentSettings.load(p).night_wb == "fixed"
    p.write_text('{"wb_r": 52, "night_wb": "auto"}')
    assert AgentSettings.load(p).night_wb == "auto"
    p.write_text("{}")
    assert AgentSettings.load(p).night_wb == "auto"
