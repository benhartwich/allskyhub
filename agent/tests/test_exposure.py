"""Auto exposure controller (SPEC §4.3)."""

import pytest

from allskyhub_agent.core.exposure import AutoExposure, Exposure, ExposureConfig, ModeLimits
from allskyhub_protocol import Mode

CFG = ExposureConfig(
    day=ModeLimits(0.35, 32, 200_000, 0.0, 0.0),
    night=ModeLimits(0.20, 32, 60_000_000, 0.0, 400.0),
)


def simulate(ae: AutoExposure, mode: Mode, radiance: float, frames: int) -> list[Exposure]:
    """Ideal linear camera: mean = radiance * seconds * gain factor, clipped at 1."""
    out: list[Exposure] = []
    cur = ae.current(mode)
    for _ in range(frames):
        factor = 10 ** (cur.gain * 0.1 / 20)
        mean = min(1.0, radiance * cur.exposure_us / 1e6 * factor)
        cur = ae.update(mode, cur, mean)
        out.append(cur)
    return out


def mean_of(e: Exposure, radiance: float) -> float:
    return min(1.0, radiance * e.exposure_us / 1e6 * 10 ** (e.gain * 0.1 / 20))


def test_converges_by_day() -> None:
    ae = AutoExposure(CFG)
    last = simulate(ae, Mode.DAY, 1000.0, 12)[-1]
    assert mean_of(last, 1000.0) == pytest.approx(0.35, rel=0.05)
    assert last.gain == 0.0


def test_night_uses_gain_only_at_max_exposure() -> None:
    ae = AutoExposure(CFG)
    last = simulate(ae, Mode.NIGHT, 0.001, 25)[-1]
    assert last.exposure_us == 60_000_000
    assert last.gain > 0
    assert mean_of(last, 0.001) == pytest.approx(0.20, rel=0.05)


def test_bright_night_lowers_gain_before_exposure() -> None:
    ae = AutoExposure(CFG, start={Mode.NIGHT: Exposure(60_000_000, 200.0)})
    nxt = ae.update(Mode.NIGHT, ae.current(Mode.NIGHT), 0.4)
    assert nxt.exposure_us == 60_000_000
    assert nxt.gain < 200.0


def test_step_is_clamped() -> None:
    ae = AutoExposure(CFG, start={Mode.DAY: Exposure(1000, 0.0)})
    nxt = ae.update(Mode.DAY, ae.current(Mode.DAY), 0.0)
    assert nxt.exposure_us <= 8000


def test_saturated_frame_recovers() -> None:
    ae = AutoExposure(CFG, start={Mode.DAY: Exposure(200_000, 0.0)})
    last = simulate(ae, Mode.DAY, 1000.0, 15)[-1]
    assert mean_of(last, 1000.0) == pytest.approx(0.35, rel=0.05)


def test_modes_remember_their_own_settings() -> None:
    ae = AutoExposure(CFG)
    simulate(ae, Mode.NIGHT, 0.001, 25)
    day = ae.current(Mode.DAY)
    assert day.exposure_us == CFG.day.min_exposure_us
    assert ae.current(Mode.NIGHT).exposure_us == 60_000_000


def test_invalid_limits_rejected() -> None:
    with pytest.raises(ValueError, match="min_exposure_us"):
        ModeLimits(0.3, 0, 100, 0, 0)
