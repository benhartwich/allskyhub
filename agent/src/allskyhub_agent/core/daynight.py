"""Day/night decision with hysteresis (SPEC §4.2)."""

from __future__ import annotations

from dataclasses import dataclass

from allskyhub_protocol import Mode


@dataclass(frozen=True)
class DayNightConfig:
    night_angle: float = -6.0
    hysteresis: float = 1.0


def next_mode(previous: Mode | None, sun_elevation: float, cfg: DayNightConfig) -> Mode:
    """Mode for a frame given the sun's elevation and the previous frame's mode.

    Below `night_angle` it is night, above `night_angle + hysteresis` it is day; in the
    band between, the previous mode is kept (day if there is none yet).
    """
    if sun_elevation < cfg.night_angle:
        return Mode.NIGHT
    if sun_elevation > cfg.night_angle + cfg.hysteresis:
        return Mode.DAY
    return previous if previous is not None else Mode.DAY
