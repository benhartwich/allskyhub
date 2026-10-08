"""Episodes: phenomena that last minutes to hours (SPEC §6.4), e.g. aurora and NLC.

Candidate frames open an episode after `confirm_frames` in a row; it grows while
candidates keep coming, is resent at most every `resend_s` and closes after `gap_s`
without one. The best frame (highest index) is its picture.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Generic, Protocol, TypeVar

from allskyhub_protocol import FrameInfo


class Scored(Protocol):
    @property
    def index(self) -> float: ...


S = TypeVar("S", bound=Scored)


@dataclass
class Episode(Generic[S]):
    start: FrameInfo
    end: datetime
    frames: int
    best: S
    best_frame: FrameInfo
    best_path: Path
    ongoing: bool = True
    last_sent: float = field(default=-math.inf)
    event_id: str | None = None  # set by the event store on the first save
    image_rev: int = 0  # SPEC §6.4: incremented whenever the picture is replaced


@dataclass(frozen=True)
class EpisodeUpdate(Generic[S]):
    episode: Episode[S]
    picture_changed: bool  # the best frame is a new one


class EpisodeTracker(Generic[S]):
    def __init__(self, confirm_frames: int, gap_s: float, resend_s: float) -> None:
        self._confirm = confirm_frames
        self._gap = gap_s
        self._resend = resend_s
        self._run: list[tuple[FrameInfo, Path, S]] = []  # candidates before opening
        self._episode: Episode[S] | None = None
        self._last_candidate: float | None = None

    def close(self) -> list[EpisodeUpdate[S]]:
        ep, self._episode = self._episode, None
        self._run = []
        self._last_candidate = None
        if ep is None:
            return []
        return [EpisodeUpdate(replace(ep, ongoing=False), picture_changed=False)]

    def tick(self, frame: FrameInfo) -> list[EpisodeUpdate[S]]:
        """Call first for every frame: closes the episode after a gap."""
        t = frame.captured_at.timestamp()
        if self._last_candidate is not None and t - self._last_candidate > self._gap:
            return self.close()
        return []

    def miss(self) -> None:
        """A frame without a candidate: a run that has not opened an episode ends."""
        self._run = []

    def hit(self, frame: FrameInfo, image_path: Path, score: S) -> list[EpisodeUpdate[S]]:
        t = frame.captured_at.timestamp()
        self._last_candidate = t
        end = frame.captured_at + timedelta(microseconds=frame.exposure_us)
        ep = self._episode
        if ep is None:
            self._run.append((frame, image_path, score))
            if len(self._run) < self._confirm:
                return []
            first = self._run[0][0]
            bf, bp, bs = max(self._run, key=lambda c: c[2].index)
            ep = Episode(first, end, len(self._run), bs, bf, bp)
            self._episode = ep
            self._run = []
            ep.last_sent = t
            return [EpisodeUpdate(ep, picture_changed=True)]
        ep.end = end
        ep.frames += 1
        better = score.index > ep.best.index
        if better:
            ep.best, ep.best_frame, ep.best_path = score, frame, image_path
        if t - ep.last_sent >= self._resend:
            ep.last_sent = t
            return [EpisodeUpdate(ep, picture_changed=better)]
        return []
