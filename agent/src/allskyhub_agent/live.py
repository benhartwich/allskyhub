"""Live state shared between the capture loop and the local web UI (SPEC §7).

The capture loop publishes each frame; the web server only reads. Everything is guarded by
one lock, and images are handed over as finished JPEG bytes.
"""

from __future__ import annotations

import io
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image as PILImage

from allskyhub_agent import __version__
from allskyhub_agent.adapters.camera import Image
from allskyhub_agent.detect.hfr import FocusHfr
from allskyhub_protocol import FrameInfo

LIVE_WIDTH = 1280
CROP_SIDE = 512
HFR_SMOOTH = 5  # frames in the shown star size: one odd frame does not move it
HFR_HISTORY = 60


def _smooth(values: deque[float]) -> float:
    """Mean of the last frames, from 5 on without the smallest and largest."""
    v = sorted(values)
    if len(v) >= HFR_SMOOTH:
        v = v[1:-1]
    return round(sum(v) / len(v), 2)


def _jpeg(img: PILImage.Image, quality: int = 85) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


@dataclass
class _State:
    frame: FrameInfo | None = None
    live_jpeg: bytes | None = None
    crop_jpeg: bytes | None = None
    sharpness: float | None = None
    sharpness_max: float | None = None
    focus_mode: bool = False
    # SPEC §7: star size (HFR, px) in focus mode, lower is sharper.
    hfr: float | None = None
    hfr_edge: float | None = None
    hfr_stars: int | None = None
    hfr_best: float | None = None
    hfr_recent: deque[float] = field(default_factory=lambda: deque(maxlen=HFR_SMOOTH))
    hfr_history: deque[float] = field(default_factory=lambda: deque(maxlen=HFR_HISTORY))
    frames: int = 0
    paused: bool = False


class LiveState:
    def __init__(self, paused_file: Path | None = None) -> None:
        self._lock = threading.Lock()
        self._s = _State()
        # SPEC §4.1: a pause survives restarts until resumed.
        self._paused_file = paused_file
        self._s.paused = paused_file is not None and paused_file.exists()

    # --- written by the capture loop -------------------------------------------------
    def publish(
        self, info: FrameInfo, image: Image, sharpness: float, hfr: FocusHfr | None = None
    ) -> None:
        pil = PILImage.fromarray(image)
        h = max(1, round(pil.height * LIVE_WIDTH / pil.width))
        live = _jpeg(pil.resize((LIVE_WIDTH, h)) if pil.width > LIVE_WIDTH else pil)
        crop: bytes | None = None
        with self._lock:
            focus = self._s.focus_mode
        if focus:
            side = min(CROP_SIDE, pil.width, pil.height)
            x0, y0 = (pil.width - side) // 2, (pil.height - side) // 2
            crop = _jpeg(pil.crop((x0, y0, x0 + side, y0 + side)), quality=92)
        with self._lock:
            s = self._s
            s.frame = info
            s.live_jpeg = live
            s.crop_jpeg = crop
            s.sharpness = sharpness
            if s.focus_mode:
                s.sharpness_max = max(s.sharpness_max or 0.0, sharpness)
                if hfr is not None:
                    self._add_hfr(s, hfr)
            s.frames += 1

    @staticmethod
    def _add_hfr(s: _State, hfr: FocusHfr) -> None:
        s.hfr_edge = hfr.edge.hfr if hfr.edge is not None else None
        if hfr.centre is None:
            s.hfr, s.hfr_stars = None, None  # too few stars (day, clouds): sharpness only
            return
        s.hfr_recent.append(hfr.centre.hfr)
        s.hfr = _smooth(s.hfr_recent)
        s.hfr_stars = hfr.centre.stars
        s.hfr_history.append(s.hfr)
        s.hfr_best = s.hfr if s.hfr_best is None else min(s.hfr_best, s.hfr)

    @staticmethod
    def _reset_hfr(s: _State) -> None:
        s.hfr = s.hfr_edge = s.hfr_stars = s.hfr_best = None
        s.hfr_recent.clear()
        s.hfr_history.clear()

    # --- used by the web UI ------------------------------------------------------------
    @property
    def focus_mode(self) -> bool:
        with self._lock:
            return self._s.focus_mode

    def set_focus_mode(self, on: bool) -> None:
        with self._lock:
            self._s.focus_mode = on
            self._s.sharpness_max = None
            self._reset_hfr(self._s)
            if not on:
                self._s.crop_jpeg = None

    @property
    def paused(self) -> bool:
        with self._lock:
            return self._s.paused

    def set_paused(self, on: bool) -> None:
        """Pause or resume capture (SPEC §4.1), kept across restarts."""
        with self._lock:
            self._s.paused = on
        f = self._paused_file
        if f is None:
            return
        try:
            if on:
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(datetime.now(UTC).isoformat() + "\n")
            else:
                f.unlink(missing_ok=True)
        except OSError:
            pass  # still paused for this run

    def reset_max(self) -> None:
        with self._lock:
            self._s.sharpness_max = None
            self._reset_hfr(self._s)

    def last_frame(self) -> FrameInfo | None:
        with self._lock:
            return self._s.frame

    def live_jpeg(self) -> bytes | None:
        with self._lock:
            return self._s.live_jpeg

    def crop_jpeg(self) -> bytes | None:
        with self._lock:
            return self._s.crop_jpeg

    def health(self) -> dict[str, object]:
        """For root helpers on the camera, without a login (SPEC §8): nothing private."""
        with self._lock:
            s = self._s
            frame = (
                {"mode": s.frame.mode.value, "captured_at": s.frame.captured_at.isoformat()}
                if s.frame
                else None
            )
            out: dict[str, object] = {"version": __version__, "frames": s.frames, "frame": frame}
            if s.paused:
                out["paused"] = True  # the watchdog does not count a pause as a stall
            return out

    def status(self) -> dict[str, object]:
        """Snapshot for the local web UI (not a protocol message)."""
        with self._lock:
            s = self._s
            return {
                "version": __version__,  # the updater's health check (SPEC §8)
                "frames": s.frames,
                "focus_mode": s.focus_mode,
                "paused": s.paused,
                "sharpness": s.sharpness,
                "sharpness_max": s.sharpness_max,
                "hfr": s.hfr,
                "hfr_edge": s.hfr_edge,
                "hfr_stars": s.hfr_stars,
                "hfr_best": s.hfr_best,
                "hfr_history": list(s.hfr_history),
                "frame": s.frame.model_dump(mode="json") if s.frame else None,
            }
