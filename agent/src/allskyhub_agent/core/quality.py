"""Bad frames (SPEC §4.9, roadmap L19): too dark or too bright, marked, never deleted."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Bad = Literal["dark", "bright"]


@dataclass(frozen=True)
class FrameQuality:
    enabled: bool = True
    dark_max: float = 0.02  # mean below this: dark
    bright_min: float = 0.90  # mean above this: bright

    def judge(self, mean: float) -> Bad | None:
        """`dark`, `bright` or None for the frame's measured mean (0..1, in the mask)."""
        if not self.enabled:
            return None
        if mean < self.dark_max:
            return "dark"
        if mean > self.bright_min:
            return "bright"
        return None
