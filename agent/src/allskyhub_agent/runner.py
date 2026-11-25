"""Capture loop (SPEC §4.1): mode → exposure → capture → store → feedback."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo

from allskyhub_agent.adapters.camera import Camera, CameraError, CaptureRequest, Frame, Image
from allskyhub_agent.core.clock import Clock
from allskyhub_agent.core.daynight import DayNightConfig, next_mode
from allskyhub_agent.core.exposure import AutoExposure, Exposure
from allskyhub_agent.core.focus import sharpness
from allskyhub_agent.core.metering import Mask, circle_mask, mean_brightness
from allskyhub_agent.core.quality import FrameQuality
from allskyhub_agent.core.sun import sun_elevation
from allskyhub_agent.core.transform import turn
from allskyhub_agent.detect.hfr import focus_hfr
from allskyhub_agent.live import LiveState
from allskyhub_agent.process import moonhdr
from allskyhub_agent.process.flat import FlatField
from allskyhub_agent.process.hotpixels import HotPixels
from allskyhub_agent.process.livestack import LiveStack
from allskyhub_agent.process.skymask import SkyMask
from allskyhub_agent.process.whitebalance import (
    Gains,
    NightWhiteBalance,
    WhiteBalanceCamera,
    inner_sky,
)
from allskyhub_agent.store.images import ImageStore, night_id
from allskyhub_protocol import FrameInfo, HdrInfo, Mode
from allskyhub_sky.astro import bright_moon_up

log = logging.getLogger(__name__)

# SPEC §4.11: the sky above about 20°, as a share of the image circle's radius.
WB_SKY_FRAC = 0.78
# Name reported for focus-mode frames, which are not stored (SPEC §7).
FOCUS_FRAME_NAME = "focus.jpg"
PAUSE_POLL_S = 2.0
DAY_OFF_POLL_S = 60.0


@dataclass(frozen=True)
class Location:
    lat: float
    lon: float


@dataclass(frozen=True)
class LoopConfig:
    day_delay_s: float = 30.0
    night_delay_s: float = 0.0


def _unknown_moon(_at: datetime, _width: int, _height: int) -> moonhdr.Point | None:
    return None  # no orientation: the largest saturated patch (SPEC §4.13)


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
        quality: FrameQuality | None = None,
        moon_mode: bool = False,
        day_capture: str = "save",
        flip: str = "none",
        rotate: int = 0,
        live_stack: LiveStack | None = None,
        white_balance: NightWhiteBalance | None = None,
        wb_hold: Callable[[], bool] | None = None,
        flat: FlatField | None = None,
        flat_min_gain: float = 0.0,
        moon_hdr: bool = False,
        moon_pixel: Callable[[datetime, int, int], moonhdr.Point | None] | None = None,
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
        self._quality = quality or FrameQuality(enabled=False)
        self._moon_mode = moon_mode
        self._moon = False
        # SPEC §4.1: by day keep (`save`), only show (`live`) or take nothing (`off`).
        self._day_capture = day_capture
        self._ephemeral: tuple[str, str] | None = None  # the last day frame kept for `live`
        self._flip = flip
        self._rotate = rotate
        self._stack = live_stack  # SPEC §4.12: only the live image
        # SPEC §4.11: only cameras whose white balance the agent can set.
        self._wb = white_balance if isinstance(camera, WhiteBalanceCamera) else None
        self._wb_hold = wb_hold or (lambda: False)
        self._flat = flat  # SPEC §4.10
        self._flat_min_gain = flat_min_gain
        # SPEC §4.13: short exposures after the next night frame when this one burnt out
        # around the moon; `moon_pixel` predicts where it stands (orientation, §4.8).
        self._hdr = moon_hdr
        self._moon_pixel = moon_pixel or _unknown_moon
        self._hdr_next = False

    @property
    def mode(self) -> Mode | None:
        return self._mode

    @property
    def moon(self) -> bool:
        """Whether the last frame was exposed in moon mode (SPEC §4.3)."""
        return self._moon

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
        changed = mode is not self._mode
        self._mode = mode

        focus = self.focus_mode
        settings = self._ae.current(mode, focus)
        if self._wb is not None and isinstance(self._cam, WhiteBalanceCamera):
            # SPEC §4.11: the learned gains at night, the normal ones by day.
            g = self._wb.gains if mode is Mode.NIGHT else Gains()
            self._cam.set_white_balance(g.red, g.blue)
        frame = self._cam.capture(CaptureRequest(settings.exposure_us, settings.gain))
        moon_up = mode is Mode.NIGHT and bright_moon_up(start, self._loc.lat, self._loc.lon)
        shorts = self._shorts() if self._hdr_next and moon_up and not focus else []
        # SPEC §4.1: turned before anything else sees the frame.
        image = turn(frame.image, self._flip, self._rotate)

        mean = mean_brightness(image, self._mask_for(image.shape[0], image.shape[1]))
        if self._sky_mask is not None and not focus:
            self._sky_mask.observe(start, night_id(start, self._tz), elevation, image)
        if self._hot is not None:
            # SPEC §4.6: the map learns from raw frames, every frame gets the correction.
            if not focus:
                self._hot.observe(start, night_id(start, self._tz), elevation, image)
            image = self._hot.apply(image)
        if self._flat is not None:
            # SPEC §4.10: twilight frames are collected before the flat, every frame gets it.
            if not focus:
                low = frame.gain <= self._flat_min_gain + 0.5
                sky = self._mask_for(image.shape[0], image.shape[1])
                self._flat.observe_twilight(start, elevation, image, mean, low, sky)
            image = self._flat.apply(image)
        shown, hdr = image, None
        if self._hdr and moon_up and not focus:
            at = self._moon_pixel(start, image.shape[1], image.shape[0])
            if shorts:
                longest = [self._prepare(f.image) for f in reversed(shorts)]
                fused = moonhdr.fuse(image, longest, at)
                if fused is not None:
                    shown = fused.image
                    hdr = HdrInfo(
                        short_exposures_us=[f.exposure_us for f in shorts],
                        short_gain=shorts[0].gain,
                        region_px=fused.region_px,
                    )
            self._hdr_next = moonhdr.wanted(image, self._mask_for(*image.shape[:2]), at)
        else:
            self._hdr_next = False
        keep = not (mode is Mode.DAY and self._day_capture == "live")
        if focus:
            nid, name = night_id(start, self._tz), FOCUS_FRAME_NAME
        else:
            stored = self._store.save(shown, start)
            nid, name = stored.night_id, stored.name
            if self._ephemeral is not None:
                self._store.remove(*self._ephemeral)  # `live`: only the newest stays
                self._ephemeral = None
            if not keep:
                self._ephemeral = (nid, name)
        self._moon = self._moon_mode and moon_up
        self._ae.update(mode, Exposure(frame.exposure_us, frame.gain), mean, focus, self._moon)

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
            bad=None if focus else self._quality.judge(mean),
            hdr=hdr,
        )
        if not focus and keep:
            self._store.append_index(info)
        learn = not focus and mode is Mode.NIGHT and info.bad is None
        if self._wb is not None and learn and self._wb.due(start):
            mask = self._mask_for(image.shape[0], image.shape[1])
            if mask is not None:
                hold = self._moon or self._wb_hold()
                self._wb.observe(start, image, inner_sky(mask, WB_SKY_FRAC), hold)
        if self._live is not None:
            if self._stack is not None:
                if focus or changed:
                    self._stack.clear()  # focus needs each frame; a new mode starts anew
                if not focus:
                    shown = self._stack.add(shown, start, frame.exposure_us)
            # SPEC §7: the star size only in focus mode, on the full frame.
            hfr = focus_hfr(image) if focus else None
            self._live.publish(info, shown, sharpness(image), hfr)
        if not focus:
            for analyze in self._analyzers:
                analyze(info, image)
        return info

    def _shorts(self) -> list[Frame]:
        """SPEC §4.13: the fixed ladder at the lowest gain, right after the long frame,
        shortest first; none when one fails (the long frame is stored as it is)."""
        try:
            return [  # the profile's lowest gain, as for the twilight flat
                self._cam.capture(CaptureRequest(us, self._flat_min_gain))
                for us in moonhdr.SHORT_US
            ]
        except CameraError as exc:
            log.warning("moon HDR: a short exposure failed: %s", exc)
            return []

    def _prepare(self, image: Image) -> Image:
        """A short frame as the stored ones: turned, hot pixels and flat applied."""
        image = turn(image, self._flip, self._rotate)
        if self._hot is not None:
            image = self._hot.apply(image)
        if self._flat is not None:
            image = self._flat.apply(image)
        return image

    def _mode_now(self) -> Mode:
        """The mode for now, as `step` would pick it (SPEC §4.2)."""
        elevation = sun_elevation(self._clock.now(), self._loc.lat, self._loc.lon)
        self._mode = next_mode(self._mode, elevation, self._dn)
        return self._mode

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
            if self._live is not None and self._live.paused:
                self._clock.sleep(PAUSE_POLL_S)  # SPEC §4.1: paused by the owner
                continue
            if self._day_capture == "off" and self._mode_now() is Mode.DAY:
                self._clock.sleep(DAY_OFF_POLL_S)  # SPEC §4.1: nothing by day
                continue
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
