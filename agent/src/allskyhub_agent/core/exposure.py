"""Auto exposure controller (SPEC §4.3).

The only place that computes exposure and gain (architecture rule 4). It works in log
space: the brightness error is turned into a damped, clamped ratio that is applied to
exposure first and to gain only when exposure is at its limit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from allskyhub_protocol import Mode

_MIN_MEAN = 1.0 / 65536.0


@dataclass(frozen=True)
class ModeLimits:
    """Limits and target for one mode."""

    target_mean: float
    min_exposure_us: int
    max_exposure_us: int
    min_gain: float
    max_gain: float
    # Size of one gain unit in dB (ZWO: 0.1 dB); comes from the hardware profile (SPEC §3).
    gain_db_per_unit: float = 0.1

    def __post_init__(self) -> None:
        if not 0 < self.target_mean < 1:
            raise ValueError("target_mean must be between 0 and 1")
        if not 1 <= self.min_exposure_us <= self.max_exposure_us:
            raise ValueError("need 1 <= min_exposure_us <= max_exposure_us")
        if not 0 <= self.min_gain <= self.max_gain:
            raise ValueError("need 0 <= min_gain <= max_gain")
        if self.gain_db_per_unit <= 0:
            raise ValueError("gain_db_per_unit must be positive")


@dataclass(frozen=True)
class ExposureConfig:
    day: ModeLimits
    night: ModeLimits
    damping: float = 0.7
    max_step: float = 8.0

    def limits(self, mode: Mode) -> ModeLimits:
        return self.day if mode is Mode.DAY else self.night


@dataclass(frozen=True)
class Exposure:
    exposure_us: int
    gain: float


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


class AutoExposure:
    """Stateful controller; one instance per camera."""

    def __init__(self, cfg: ExposureConfig, start: dict[Mode, Exposure] | None = None) -> None:
        self._cfg = cfg
        self._last: dict[Mode, Exposure] = {
            Mode.DAY: Exposure(cfg.day.min_exposure_us, cfg.day.min_gain),
            Mode.NIGHT: Exposure(cfg.night.max_exposure_us // 4, cfg.night.min_gain),
        }
        if start:
            self._last.update(start)

    def current(self, mode: Mode) -> Exposure:
        """Settings to use for the next frame in `mode` (SPEC §4.3: per-mode memory)."""
        return self._last[mode]

    def update(self, mode: Mode, used: Exposure, measured_mean: float) -> Exposure:
        """Feed back the result of a frame taken with `used`; returns the next settings."""
        lim = self._cfg.limits(mode)
        ratio = lim.target_mean / max(measured_mean, _MIN_MEAN)
        ratio = _clamp(ratio**self._cfg.damping, 1 / self._cfg.max_step, self._cfg.max_step)

        exposure = float(used.exposure_us)
        gain = used.gain
        # Gain as a linear factor on top of the mode's minimum gain.
        gain_factor = self._gain_factor(gain, lim)

        if ratio >= 1.0:
            # Too dark: lengthen exposure first, then raise gain.
            want = exposure * ratio
            exposure = min(want, lim.max_exposure_us)
            rest = want / exposure if exposure > 0 else 1.0
            gain_factor *= rest
        else:
            # Too bright: lower gain first, then shorten exposure.
            want_gain = gain_factor * ratio
            gain_factor = max(want_gain, 1.0)
            rest = want_gain / gain_factor
            exposure *= rest

        exposure = _clamp(exposure, lim.min_exposure_us, lim.max_exposure_us)
        nxt = Exposure(round(exposure), self._gain_from_factor(gain_factor, lim))
        self._last[mode] = nxt
        return nxt

    @staticmethod
    def _gain_factor(gain: float, lim: ModeLimits) -> float:
        return 10 ** ((gain - lim.min_gain) * lim.gain_db_per_unit / 20.0)

    @staticmethod
    def _gain_from_factor(factor: float, lim: ModeLimits) -> float:
        g = lim.min_gain + 20.0 * math.log10(max(factor, 1.0)) / lim.gain_db_per_unit
        return round(_clamp(g, lim.min_gain, lim.max_gain), 1)
