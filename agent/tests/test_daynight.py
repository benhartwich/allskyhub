"""Day/night decision (SPEC §4.2)."""

from itertools import pairwise

from allskyhub_agent.core.daynight import DayNightConfig, next_mode
from allskyhub_protocol import Mode

CFG = DayNightConfig(night_angle=-6.0, hysteresis=1.0)


def test_clear_cases() -> None:
    assert next_mode(None, 10.0, CFG) is Mode.DAY
    assert next_mode(Mode.DAY, -7.0, CFG) is Mode.NIGHT
    assert next_mode(Mode.NIGHT, -4.0, CFG) is Mode.DAY


def test_band_keeps_previous_mode() -> None:
    assert next_mode(Mode.NIGHT, -5.5, CFG) is Mode.NIGHT
    assert next_mode(Mode.DAY, -5.5, CFG) is Mode.DAY
    assert next_mode(None, -5.5, CFG) is Mode.DAY


def test_no_flapping_around_threshold() -> None:
    mode = Mode.DAY
    modes: list[Mode] = []
    for e in (-5.9, -6.05, -5.95, -6.1, -5.8, -5.3, -5.6, -4.9):
        mode = next_mode(mode, e, CFG)
        modes.append(mode)
    # One switch to night, one back to day once above -5.0.
    switches = sum(1 for a, b in pairwise(modes) if a is not b)
    assert switches == 2
    assert modes[-1] is Mode.DAY
