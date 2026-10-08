"""Hardware profiles (SPEC §3). Values for real cameras are first estimates until M1."""

from __future__ import annotations

from dataclasses import dataclass

from allskyhub_agent.core.exposure import ExposureConfig, ModeLimits


@dataclass(frozen=True)
class Profile:
    id: str
    camera: str
    exposure: ExposureConfig
    image_circle_frac: float = 0.48  # radius relative to the short image side
    # SQM zero point (SPEC §6.3 sky): calibrated for the ASI678MC against a dark sky, a
    # first guess for the others until calibration (roadmap #8).
    sqm_offset: float = 18.8


def _limits(max_night_us: int, max_gain: float, gain_db: float) -> ExposureConfig:
    return ExposureConfig(
        day=ModeLimits(0.35, 32, 200_000, 0.0, 0.0, gain_db),
        night=ModeLimits(0.20, 32, max_night_us, 0.0, max_gain, gain_db),
    )


PROFILES: dict[str, Profile] = {
    "sim": Profile("sim", "sim", _limits(60_000_000, 400.0, 0.1)),
    "zwo-asi678mc": Profile("zwo-asi678mc", "zwo", _limits(60_000_000, 400.0, 0.1)),
    # Raspberry Pi HQ (IMX477): analogue gain 1-22.3x = 0-27 dB, in 0.1 dB units like ZWO;
    # the libcamera driver allows exposures up to about 230 s.
    "rpi-hq": Profile("rpi-hq", "libcamera", _limits(200_000_000, 260.0, 0.1)),
}


def get_profile(profile_id: str) -> Profile:
    try:
        return PROFILES[profile_id]
    except KeyError:
        raise ValueError(f"unknown profile {profile_id!r} (known: {', '.join(PROFILES)})") from None
