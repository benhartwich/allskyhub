"""Auto exposure controller (SPEC §4.3).

The only place that computes exposure and gain (architecture rule 4). It works in log
space: the brightness error is turned into a damped, clamped ratio. The resulting amount
of light is put into exposure as far as the mode allows; only the rest goes into gain.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

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
    # Focus mode (SPEC §7) needs quick feedback: exposure is capped, gain makes up the rest.
    focus_max_exposure_us: int = 2_000_000

    def limits(self, mode: Mode, focus: bool = False) -> ModeLimits:
        lim = self.day if mode is Mode.DAY else self.night
        if focus and lim.max_exposure_us > self.focus_max_exposure_us:
            lim = replace(
                lim,
                max_exposure_us=max(lim.min_exposure_us, self.focus_max_exposure_us),
            )
        return lim


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

    def current(self, mode: Mode, focus: bool = False) -> Exposure:
        """Settings to use for the next frame in `mode` (SPEC §4.3: per-mode memory).

        In focus mode an exposure above the focus cap is shortened and the difference is moved
        into gain (as far as the gain limit allows), so the brightness stays about the same.
        """
        last = self._last[mode]
        lim = self._cfg.limits(mode, focus)
        if last.exposure_us <= lim.max_exposure_us:
            return last
        factor = self._gain_factor(last.gain, lim) * last.exposure_us / lim.max_exposure_us
        return Exposure(lim.max_exposure_us, self._gain_from_factor(factor, lim))

    def update(
        self, mode: Mode, used: Exposure, measured_mean: float, focus: bool = False
    ) -> Exposure:
        """Feed back the result of a frame taken with `used`; returns the next settings."""
        lim = self._cfg.limits(mode, focus)
        ratio = lim.target_mean / max(measured_mean, _MIN_MEAN)
        ratio = _clamp(ratio**self._cfg.damping, 1 / self._cfg.max_step, self._cfg.max_step)

        # The wanted amount of light, as exposure (µs) x linear gain factor. It is always split
        # with as much exposure as the mode allows and as little gain as needed: gain adds
        # noise, so a gain left high (e.g. after focus mode) is traded back for exposure.
        total = used.exposure_us * self._gain_factor(used.gain, lim) * ratio
        exposure = _clamp(total, lim.min_exposure_us, lim.max_exposure_us)
        gain_factor = max(1.0, total / exposure)

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
