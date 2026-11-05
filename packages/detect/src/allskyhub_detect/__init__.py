"""allskyhub meteor detection, shared by the camera agent and the hub: streaks in the
difference of consecutive frames and the checks of one frame and its neighbours
(``candidates``). Following streaks over many frames, the sky checks and showers stay
with the camera agent."""

from allskyhub_detect.meteor import (
    Candidate,
    MeteorConfig,
    Streak,
    candidates,
    difference_streaks,
    find_streaks,
    to_gray,
    track_px,
)

__all__ = [
    "Candidate",
    "MeteorConfig",
    "Streak",
    "candidates",
    "difference_streaks",
    "find_streaks",
    "to_gray",
    "track_px",
]
