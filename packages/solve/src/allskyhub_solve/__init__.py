"""allskyhub plate solving (SPEC §4.8), shared by the camera agent and the hub: a frame's
lens and orientation from its stars, the acceptance rule, and the local fit of a bright
meteor's track against the stars around it."""

from allskyhub_solve.solve import (
    MAX_RMS_DEG,
    MIN_STARS,
    Solution,
    Verdict,
    accept,
    solve,
    solve_verdict,
)

__all__ = ["MAX_RMS_DEG", "MIN_STARS", "Solution", "Verdict", "accept", "solve", "solve_verdict"]
