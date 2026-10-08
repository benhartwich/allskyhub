"""Live state shared between the capture loop and the local web UI (SPEC §7).

The capture loop publishes each frame; the web server only reads. Everything is guarded by
one lock, and images are handed over as finished JPEG bytes.
"""

from __future__ import annotations

import io
import threading
from dataclasses import dataclass

from PIL import Image as PILImage

from allskyhub_agent import __version__
from allskyhub_agent.adapters.camera import Image
from allskyhub_protocol import FrameInfo

LIVE_WIDTH = 1280
CROP_SIDE = 512


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
    frames: int = 0


class LiveState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._s = _State()

    # --- written by the capture loop -------------------------------------------------
    def publish(self, info: FrameInfo, image: Image, sharpness: float) -> None:
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
            s.frames += 1

    # --- used by the web UI ------------------------------------------------------------
    @property
    def focus_mode(self) -> bool:
        with self._lock:
            return self._s.focus_mode

    def set_focus_mode(self, on: bool) -> None:
        with self._lock:
            self._s.focus_mode = on
            self._s.sharpness_max = None
            if not on:
                self._s.crop_jpeg = None

    def reset_max(self) -> None:
        with self._lock:
            self._s.sharpness_max = None

    def last_frame(self) -> FrameInfo | None:
        with self._lock:
            return self._s.frame

    def live_jpeg(self) -> bytes | None:
        with self._lock:
            return self._s.live_jpeg

    def crop_jpeg(self) -> bytes | None:
        with self._lock:
            return self._s.crop_jpeg

    def status(self) -> dict[str, object]:
        """Snapshot for the local web UI (not a protocol message)."""
        with self._lock:
            s = self._s
            return {
                "version": __version__,  # the updater's health check (SPEC §8)
                "frames": s.frames,
                "focus_mode": s.focus_mode,
                "sharpness": s.sharpness,
                "sharpness_max": s.sharpness_max,
                "frame": s.frame.model_dump(mode="json") if s.frame else None,
            }
