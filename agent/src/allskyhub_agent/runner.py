"""Capture loop (SPEC §4.1): mode → exposure → capture → store → feedback."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, tzinfo

from allskyhub_agent.adapters.camera import Camera, CameraError, CaptureRequest, Image
from allskyhub_agent.core.clock import Clock
from allskyhub_agent.core.daynight import DayNightConfig, next_mode
from allskyhub_agent.core.exposure import AutoExposure, Exposure
from allskyhub_agent.core.focus import sharpness
from allskyhub_agent.core.metering import Mask, circle_mask, mean_brightness
from allskyhub_agent.core.sun import sun_elevation
from allskyhub_agent.live import LiveState
from allskyhub_agent.process.hotpixels import HotPixels
from allskyhub_agent.process.skymask import SkyMask
from allskyhub_agent.store.images import ImageStore, night_id
from allskyhub_protocol import FrameInfo, Mode

log = logging.getLogger(__name__)

# Name reported for focus-mode frames, which are not stored (SPEC §7).
FOCUS_FRAME_NAME = "focus.jpg"


@dataclass(frozen=True)
class Location:
    lat: float
    lon: float


@dataclass(frozen=True)
class LoopConfig:
    day_delay_s: float = 30.0
    night_delay_s: float = 0.0


class Runner:
    def __init__(
        self,
        camera: Camera,
        auto_exposure: AutoExposure,
        store: ImageStore,
        clock: Clock,
        location: Location,
        profile: str,
        daynight: DayNightConfig | None = None,
        loop: LoopConfig | None = None,
        mask_radius_frac: float | None = None,
        live: LiveState | None = None,
        analyzers: list[Callable[[FrameInfo, Image], None]] | None = None,
        local_tz: tzinfo = UTC,
        hot_pixels: HotPixels | None = None,
        sky_mask: SkyMask | None = None,
    ) -> None:
        self._cam = camera
        self._ae = auto_exposure
        self._store = store
        self._clock = clock
        self._loc = location
        self._profile = profile
        self._dn = daynight or DayNightConfig()
        self._loop = loop or LoopConfig()
        self._mask_frac = mask_radius_frac
        self._mask: Mask | None = None
        self._mode: Mode | None = None
        self._live = live
        # Called with every stored frame and its pixels, e.g. the detection worker.
        self._analyzers = analyzers or []
        self._stop = threading.Event()
        self._tz = local_tz
        self._hot = hot_pixels
        self._sky_mask = sky_mask

    @property
    def mode(self) -> Mode | None:
        return self._mode

    @property
    def focus_mode(self) -> bool:
        return self._live is not None and self._live.focus_mode

    def step(self) -> FrameInfo:
        """Take, store and evaluate one frame; does not wait afterwards.

        In focus mode (SPEC §7) frames are only published to the live view, not stored.
        """
        start = self._clock.now()
        elevation = sun_elevation(start, self._loc.lat, self._loc.lon)
        mode = next_mode(self._mode, elevation, self._dn)
        self._mode = mode

        focus = self.focus_mode
        settings = self._ae.current(mode, focus)
        frame = self._cam.capture(CaptureRequest(settings.exposure_us, settings.gain))

        mean = mean_brightness(
            frame.image, self._mask_for(frame.image.shape[0], frame.image.shape[1])
        )
        image = frame.image
        if self._sky_mask is not None and not focus:
            self._sky_mask.observe(start, night_id(start, self._tz), elevation, image)
        if self._hot is not None:
            # SPEC §4.6: the map learns from raw frames, every frame gets the correction.
            if not focus:
                self._hot.observe(start, night_id(start, self._tz), elevation, image)
            image = self._hot.apply(image)
        if focus:
            nid, name = night_id(start, self._tz), FOCUS_FRAME_NAME
        else:
            stored = self._store.save(image, start)
            nid, name = stored.night_id, stored.name
        self._ae.update(mode, Exposure(frame.exposure_us, frame.gain), mean, focus)

        info = FrameInfo(
            captured_at=start,
            night_id=nid,
            name=name,
            mode=mode,
            exposure_us=frame.exposure_us,
            gain=frame.gain,
            mean=round(mean, 5),
            sun_elevation=round(elevation, 2),
            sensor_temp_c=frame.sensor_temp_c,
            profile=self._profile,
        )
        if not focus:
            self._store.append_index(info)
        if self._live is not None:
            self._live.publish(info, image, sharpness(image))
        if not focus:
            for analyze in self._analyzers:
                analyze(info, image)
        return info

    def _mask_for(self, height: int, width: int) -> Mask | None:
        """Image-circle mask for the frame size, built once (SPEC §4.3: metering in the mask)."""
        if self._sky_mask is not None:
            learned = self._sky_mask.get(height, width)
            if learned is not None:
                return learned
        if self._mask_frac is None:
            return None
        if self._mask is None or self._mask.shape != (height, width):
            self._mask = circle_mask(height, width, self._mask_frac)
        return self._mask

    def stop(self) -> None:
        """End `run()` after the current frame (e.g. to apply new settings)."""
        self._stop.set()

    def delay(self) -> float:
        if self.focus_mode:
            return 0.0
        return self._loop.night_delay_s if self._mode is Mode.NIGHT else self._loop.day_delay_s

    def run(
        self, frames: int | None = None, on_frame: Callable[[FrameInfo], None] | None = None
    ) -> int:
        """Capture `frames` frames (forever if None); returns how many were taken.

        A failed capture or a full disk never ends the loop: it is logged, and the next try
        waits 5 s, doubling to at most 5 min while the failures go on.
        """
        n = 0
        failures = 0
        while (frames is None or n < frames) and not self._stop.is_set():
            try:
                info = self.step()
            except (CameraError, OSError) as exc:
                failures += 1
                wait = min(300.0, 5.0 * 2 ** (failures - 1))
                log.warning(
                    "capture failed (%d in a row): %s; retrying in %.0f s", failures, exc, wait
                )
                self._clock.sleep(wait)
                continue
            failures = 0
            n += 1
            if on_frame is not None:
                on_frame(info)
            self._clock.sleep(self.delay())
        return n
