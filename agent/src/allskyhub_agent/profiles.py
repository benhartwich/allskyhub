"""Hardware profiles (SPEC §3). Values for real cameras are first estimates until M1."""

from __future__ import annotations

from dataclasses import dataclass, replace

from allskyhub_agent.core.exposure import ExposureConfig, ModeLimits
from allskyhub_agent.settings import AgentSettings


@dataclass(frozen=True)
class Profile:
    id: str
    camera: str
    exposure: ExposureConfig
    image_circle_frac: float = 0.48  # radius relative to the short image side
    # SQM zero point (SPEC §6.3 sky): calibrated for the ASI678MC against a dark sky, a
    # first guess for the others until calibration (roadmap #8).
    sqm_offset: float = 18.8
    # The longest night exposure the owner may set (roadmap L7); the profile's own night
    # limit stays the default for an empty field. 0: the same as the default.
    night_exposure_cap_us: int = 0

    @property
    def night_cap_us(self) -> int:
        return max(self.night_exposure_cap_us, self.exposure.night.max_exposure_us)


def _limits(max_night_us: int, max_gain: float, gain_db: float) -> ExposureConfig:
    return ExposureConfig(
        day=ModeLimits(0.35, 32, 200_000, 0.0, 0.0, gain_db),
        night=ModeLimits(0.20, 32, max_night_us, 0.0, max_gain, gain_db),
    )


PROFILES: dict[str, Profile] = {
    # 60 s by default; Benjamin, 2026-10-12: up to 300 s when set explicitly.
    "sim": Profile(
        "sim", "sim", _limits(60_000_000, 400.0, 0.1), night_exposure_cap_us=300_000_000
    ),
    "zwo-asi678mc": Profile(
        "zwo-asi678mc", "zwo", _limits(60_000_000, 400.0, 0.1), night_exposure_cap_us=300_000_000
    ),
    # Raspberry Pi HQ (IMX477): analogue gain 1-22.3x = 0-27 dB, in 0.1 dB units like ZWO;
    # the libcamera driver allows exposures up to about 230 s.
    "rpi-hq": Profile("rpi-hq", "libcamera", _limits(200_000_000, 260.0, 0.1)),
}


def get_profile(profile_id: str) -> Profile:
    try:
        return PROFILES[profile_id]
    except KeyError:
        raise ValueError(f"unknown profile {profile_id!r} (known: {', '.join(PROFILES)})") from None


def with_night_limits(profile: Profile, s: AgentSettings) -> Profile:
    """The profile with the night limits from the settings (roadmap L7), never beyond what
    the profile allows (`night_cap_us`; an empty field keeps the profile's default); the
    auto exposure (SPEC §4.3) stays the only place that picks exposure and gain."""
    night = profile.exposure.night
    max_us = night.max_exposure_us
    if s.night_max_exposure_s is not None:
        cap = profile.night_cap_us
        max_us = max(night.min_exposure_us, min(cap, round(s.night_max_exposure_s * 1e6)))
    max_gain = night.max_gain
    if s.night_max_gain is not None:
        max_gain = max(night.min_gain, min(max_gain, float(s.night_max_gain)))
    night = replace(night, max_exposure_us=max_us, max_gain=max_gain)
    return replace(profile, exposure=replace(profile.exposure, night=night))
