"""ZWO adapter against a fake SDK (no hardware needed)."""

from __future__ import annotations

import numpy as np
import pytest

from allskyhub_agent.adapters.asi_sdk import (
    CameraInfo,
    Control,
    ControlCaps,
    ExposureStatus,
    ImageType,
)
from allskyhub_agent.adapters.camera import CaptureRequest
from allskyhub_agent.adapters.zwo import CameraError, ZwoCamera, ZwoSettings


class FakeAsi:
    def __init__(self, width: int = 64, height: int = 36, finish_after: int = 2) -> None:
        self.info = CameraInfo(0, "ZWO ASI678MC", width, height, True, 2.0, 12)
        self.calls: list[tuple[str, tuple[int, ...]]] = []
        self.values: dict[int, int] = {Control.TEMPERATURE: 215}
        self.finish_after = finish_after
        self.fail = False
        self._polls = 0
        self.roi: tuple[int, int, int, int] = (0, 0, 0, 0)
        self.opened = False

    def num_cameras(self) -> int:
        return 1

    def camera_info(self, index: int) -> CameraInfo:
        return self.info

    def open(self, camera_id: int) -> None:
        self.opened = True

    def close(self, camera_id: int) -> None:
        self.opened = False

    def controls(self, camera_id: int) -> list[ControlCaps]:
        writable = (
            Control.GAIN,
            Control.EXPOSURE,
            Control.WB_R,
            Control.WB_B,
            Control.BANDWIDTHOVERLOAD,
        )
        caps = [ControlCaps(int(c), c.name, 0, 1000, 0, True) for c in writable]
        caps.append(ControlCaps(int(Control.TEMPERATURE), "Temperature", -500, 1000, 0, False))
        return caps

    def set_control(self, camera_id: int, control: int, value: int) -> None:
        self.calls.append(("set", (control, value)))
        self.values[control] = value

    def get_control(self, camera_id: int, control: int) -> int:
        return self.values.get(control, 0)

    def set_roi(self, camera_id: int, width: int, height: int, binning: int, img: int) -> None:
        self.roi = (width, height, binning, img)

    def start_exposure(self, camera_id: int) -> None:
        self.calls.append(("start", ()))
        self._polls = 0

    def stop_exposure(self, camera_id: int) -> None:
        self.calls.append(("stop", ()))

    def exposure_status(self, camera_id: int) -> ExposureStatus:
        if self.fail:
            return ExposureStatus.FAILED
        self._polls += 1
        return ExposureStatus.SUCCESS if self._polls > self.finish_after else ExposureStatus.WORKING

    def read_data(self, camera_id: int, size: int) -> bytes:
        w, h = self.roi[0], self.roi[1]
        bgr = np.zeros((h, w, 3), dtype=np.uint8)
        bgr[:, :, 0] = 10  # blue
        bgr[:, :, 2] = 200  # red
        return bgr.tobytes()


class FakeTime:
    def __init__(self) -> None:
        self.t = 0.0
        self.slept: list[float] = []

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.t += s

    def monotonic(self) -> float:
        return self.t


def make(api: FakeAsi, settings: ZwoSettings | None = None) -> tuple[ZwoCamera, FakeTime]:
    ft = FakeTime()
    return ZwoCamera(api, settings=settings, sleep=ft.sleep, monotonic=ft.monotonic), ft


def test_open_sets_roi_and_bandwidth() -> None:
    api = FakeAsi(width=3840, height=2160)
    cam, _ = make(api)
    assert api.opened
    assert api.roi == (3840, 2160, 1, ImageType.RGB24)
    assert ("set", (Control.BANDWIDTHOVERLOAD, 40)) in api.calls
    cam.close()
    assert not api.opened


def test_roi_rounded_to_sdk_multiples() -> None:
    api = FakeAsi(width=3001, height=2001)
    make(api, ZwoSettings(binning=2))
    assert api.roi[:3] == (1496, 1000, 2)


def test_capture_sets_manual_exposure_and_returns_rgb() -> None:
    api = FakeAsi()
    cam, ft = make(api)
    frame = cam.capture(CaptureRequest(exposure_us=2_000_000, gain=150.4))
    assert ("set", (Control.GAIN, 150)) in api.calls
    assert ("set", (Control.EXPOSURE, 2_000_000)) in api.calls
    assert frame.image.shape == (36, 64, 3)
    assert frame.image[0, 0].tolist() == [200, 0, 10]  # BGR from the SDK turned into RGB
    assert frame.sensor_temp_c == pytest.approx(21.5)
    # Slept through the exposure before polling.
    assert ft.slept[0] == pytest.approx(1.9)


def test_wb_only_set_when_configured() -> None:
    api = FakeAsi()
    make(api)
    assert not any(c == ("set", (Control.WB_R, v)) for c in api.calls for v in range(1000))
    api2 = FakeAsi()
    make(api2, ZwoSettings(wb_r=52, wb_b=95))
    assert ("set", (Control.WB_R, 52)) in api2.calls
    assert ("set", (Control.WB_B, 95)) in api2.calls


def test_failed_exposure_raises() -> None:
    api = FakeAsi()
    cam, _ = make(api)
    api.fail = True
    with pytest.raises(CameraError, match="failed"):
        cam.capture(CaptureRequest(1000, 0))


def test_timeout_stops_exposure() -> None:
    api = FakeAsi(finish_after=10**9)
    cam, _ = make(api, ZwoSettings(readout_timeout_s=1.0, poll_s=0.25))
    with pytest.raises(CameraError, match="in time"):
        cam.capture(CaptureRequest(1000, 0))
    assert api.calls[-1] == ("stop", ())
