"""ZWO ASI camera adapter (SPEC §3, profile `zwo-asi678mc`).

Uses snap mode (`ASIStartExposure`), which handles exposures from microseconds to many
minutes, and reads RGB24 frames. Exposure and gain are always set manually: the agent's
own controller decides them (architecture rule 4), never the camera's auto modes.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from allskyhub_agent.adapters.asi_sdk import (
    AsiApi,
    CameraInfo,
    Control,
    ExposureStatus,
    ImageType,
)
from allskyhub_agent.adapters.camera import CaptureRequest, Frame, Image


class CameraError(RuntimeError):
    pass


@dataclass(frozen=True)
class ZwoSettings:
    binning: int = 1
    wb_r: int | None = None  # None: keep the camera default
    wb_b: int | None = None
    offset: int | None = None
    bandwidth: int = 40  # USB bandwidth percent; low values are more robust on a Pi
    readout_timeout_s: float = 30.0
    poll_s: float = 0.05


class ZwoCamera:
    """`Camera` implementation for ZWO ASI cameras."""

    def __init__(
        self,
        api: AsiApi,
        index: int = 0,
        settings: ZwoSettings | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._api = api
        self._cfg = settings or ZwoSettings()
        self._sleep = sleep
        self._monotonic = monotonic
        if api.num_cameras() <= index:
            raise CameraError(f"no ZWO camera at index {index}")
        self._info: CameraInfo = api.camera_info(index)
        self._id = self._info.camera_id
        api.open(self._id)
        self._open = True
        b = self._cfg.binning
        # Width must be a multiple of 8 and height of 2 (SDK rule).
        self._w = (self._info.width // b) // 8 * 8
        self._h = (self._info.height // b) // 2 * 2
        api.set_roi(self._id, self._w, self._h, b, ImageType.RGB24)
        caps = {c.control: c for c in api.controls(self._id)}
        self._writable = {k for k, c in caps.items() if c.writable}
        self._set(Control.BANDWIDTHOVERLOAD, self._cfg.bandwidth)
        for control, value in (
            (Control.WB_R, self._cfg.wb_r),
            (Control.WB_B, self._cfg.wb_b),
            (Control.OFFSET, self._cfg.offset),
        ):
            if value is not None:
                self._set(control, value)

    @property
    def name(self) -> str:
        return self._info.name

    @property
    def info(self) -> CameraInfo:
        return self._info

    def _set(self, control: Control, value: int) -> None:
        if control in self._writable:
            self._api.set_control(self._id, control, value)

    def capture(self, req: CaptureRequest) -> Frame:
        self._set(Control.GAIN, round(req.gain))
        self._set(Control.EXPOSURE, max(1, req.exposure_us))
        self._api.start_exposure(self._id)

        # Sleep through most of the exposure, then poll for the end.
        self._sleep(max(0.0, req.exposure_us / 1e6 - 0.1))
        deadline = self._monotonic() + self._cfg.readout_timeout_s
        while True:
            status = self._api.exposure_status(self._id)
            if status is ExposureStatus.SUCCESS:
                break
            if status is ExposureStatus.FAILED:
                raise CameraError("exposure failed")
            if self._monotonic() > deadline:
                self._api.stop_exposure(self._id)
                raise CameraError("exposure did not finish in time")
            self._sleep(self._cfg.poll_s)

        size = self._w * self._h * 3
        data = self._api.read_data(self._id, size)
        # ndarray(buffer=...) instead of frombuffer(): numpy 2.5's frombuffer stub is partially
        # unknown to pyright strict.
        bgr = np.ndarray((self._h, self._w, 3), dtype=np.uint8, buffer=data[:size])
        rgb: Image = bgr[:, :, ::-1].copy()  # the SDK delivers BGR
        temp = self._api.get_control(self._id, Control.TEMPERATURE) / 10.0
        return Frame(image=rgb, exposure_us=req.exposure_us, gain=req.gain, sensor_temp_c=temp)

    def close(self) -> None:
        if self._open:
            self._open = False
            self._api.close(self._id)
