"""ctypes bindings for the ZWO ASI camera SDK (libASICamera2, MIT licensed by ZWO).

Only the calls the agent needs. The library itself is not part of this repository; the
Pi image installs it (SPEC §8) and the agent loads it from a configured path.
Struct layouts follow ASICamera2.h of SDK 1.39.
"""

from __future__ import annotations

import ctypes as ct
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Protocol


class AsiError(RuntimeError):
    def __init__(self, call: str, code: int) -> None:
        super().__init__(f"{call} failed: {_ERROR_NAMES.get(code, 'unknown')} ({code})")
        self.call = call
        self.code = code


_ERROR_NAMES = {
    1: "invalid index",
    2: "invalid id",
    3: "invalid control type",
    4: "camera closed",
    5: "camera removed",
    6: "invalid path",
    7: "invalid file format",
    8: "invalid size",
    9: "invalid image type",
    10: "outside of boundary",
    11: "timeout",
    12: "invalid sequence",
    13: "buffer too small",
    14: "video mode active",
    15: "exposure in progress",
    16: "general error",
    17: "invalid mode",
}


class Control(IntEnum):
    GAIN = 0
    EXPOSURE = 1
    GAMMA = 2
    WB_R = 3
    WB_B = 4
    OFFSET = 5
    BANDWIDTHOVERLOAD = 6
    TEMPERATURE = 8  # returns 10 x °C
    FLIP = 9


class ImageType(IntEnum):
    RAW8 = 0
    RGB24 = 1
    RAW16 = 2
    Y8 = 3


class ExposureStatus(IntEnum):
    IDLE = 0
    WORKING = 1
    SUCCESS = 2
    FAILED = 3


class _CameraInfo(ct.Structure):
    _fields_ = (
        ("Name", ct.c_char * 64),
        ("CameraID", ct.c_int),
        ("MaxHeight", ct.c_long),
        ("MaxWidth", ct.c_long),
        ("IsColorCam", ct.c_int),
        ("BayerPattern", ct.c_int),
        ("SupportedBins", ct.c_int * 16),
        ("SupportedVideoFormat", ct.c_int * 8),
        ("PixelSize", ct.c_double),
        ("MechanicalShutter", ct.c_int),
        ("ST4Port", ct.c_int),
        ("IsCoolerCam", ct.c_int),
        ("IsUSB3Host", ct.c_int),
        ("IsUSB3Camera", ct.c_int),
        ("ElecPerADU", ct.c_float),
        ("BitDepth", ct.c_int),
        ("IsTriggerCam", ct.c_int),
        ("Unused", ct.c_char * 16),
    )


class _ControlCaps(ct.Structure):
    _fields_ = (
        ("Name", ct.c_char * 64),
        ("Description", ct.c_char * 128),
        ("MaxValue", ct.c_long),
        ("MinValue", ct.c_long),
        ("DefaultValue", ct.c_long),
        ("IsAutoSupported", ct.c_int),
        ("IsWritable", ct.c_int),
        ("ControlType", ct.c_int),
        ("Unused", ct.c_char * 32),
    )


@dataclass(frozen=True)
class CameraInfo:
    camera_id: int
    name: str
    width: int
    height: int
    is_color: bool
    pixel_size_um: float
    bit_depth: int


@dataclass(frozen=True)
class ControlCaps:
    control: int
    name: str
    min_value: int
    max_value: int
    default: int
    writable: bool


class AsiApi(Protocol):
    """What `ZwoCamera` needs from the SDK; implemented by `AsiSdk` and by test fakes."""

    def num_cameras(self) -> int: ...
    def camera_info(self, index: int) -> CameraInfo: ...
    def open(self, camera_id: int) -> None: ...
    def close(self, camera_id: int) -> None: ...
    def controls(self, camera_id: int) -> list[ControlCaps]: ...
    def set_control(self, camera_id: int, control: int, value: int) -> None: ...
    def get_control(self, camera_id: int, control: int) -> int: ...
    def set_roi(self, camera_id: int, width: int, height: int, binning: int, img: int) -> None: ...
    def start_exposure(self, camera_id: int) -> None: ...
    def stop_exposure(self, camera_id: int) -> None: ...
    def exposure_status(self, camera_id: int) -> ExposureStatus: ...
    def read_data(self, camera_id: int, size: int) -> bytes: ...


class AsiSdk:
    """`AsiApi` on top of libASICamera2.so."""

    def __init__(self, library: Path) -> None:
        self._lib = ct.CDLL(str(library))
        lib = self._lib
        lib.ASIGetNumOfConnectedCameras.restype = ct.c_int
        lib.ASIGetCameraProperty.argtypes = (ct.POINTER(_CameraInfo), ct.c_int)
        lib.ASIGetControlCaps.argtypes = (ct.c_int, ct.c_int, ct.POINTER(_ControlCaps))
        lib.ASIGetNumOfControls.argtypes = (ct.c_int, ct.POINTER(ct.c_int))
        lib.ASISetControlValue.argtypes = (ct.c_int, ct.c_int, ct.c_long, ct.c_int)
        lib.ASIGetControlValue.argtypes = (
            ct.c_int,
            ct.c_int,
            ct.POINTER(ct.c_long),
            ct.POINTER(ct.c_int),
        )
        lib.ASISetROIFormat.argtypes = (ct.c_int, ct.c_int, ct.c_int, ct.c_int, ct.c_int)
        lib.ASIStartExposure.argtypes = (ct.c_int, ct.c_int)
        lib.ASIGetExpStatus.argtypes = (ct.c_int, ct.POINTER(ct.c_int))
        lib.ASIGetDataAfterExp.argtypes = (ct.c_int, ct.c_char_p, ct.c_long)

    def _check(self, call: str, code: int) -> None:
        if code != 0:
            raise AsiError(call, code)

    def num_cameras(self) -> int:
        return int(self._lib.ASIGetNumOfConnectedCameras())

    def camera_info(self, index: int) -> CameraInfo:
        info = _CameraInfo()
        self._check("ASIGetCameraProperty", self._lib.ASIGetCameraProperty(ct.byref(info), index))
        return CameraInfo(
            camera_id=int(info.CameraID),
            name=bytes(info.Name).split(b"\0", 1)[0].decode(errors="replace"),
            width=int(info.MaxWidth),
            height=int(info.MaxHeight),
            is_color=bool(info.IsColorCam),
            pixel_size_um=float(info.PixelSize),
            bit_depth=int(info.BitDepth),
        )

    def open(self, camera_id: int) -> None:
        self._check("ASIOpenCamera", self._lib.ASIOpenCamera(camera_id))
        self._check("ASIInitCamera", self._lib.ASIInitCamera(camera_id))

    def close(self, camera_id: int) -> None:
        self._check("ASICloseCamera", self._lib.ASICloseCamera(camera_id))

    def controls(self, camera_id: int) -> list[ControlCaps]:
        n = ct.c_int()
        self._check("ASIGetNumOfControls", self._lib.ASIGetNumOfControls(camera_id, ct.byref(n)))
        out: list[ControlCaps] = []
        for i in range(n.value):
            caps = _ControlCaps()
            self._check(
                "ASIGetControlCaps", self._lib.ASIGetControlCaps(camera_id, i, ct.byref(caps))
            )
            out.append(
                ControlCaps(
                    control=int(caps.ControlType),
                    name=bytes(caps.Name).split(b"\0", 1)[0].decode(errors="replace"),
                    min_value=int(caps.MinValue),
                    max_value=int(caps.MaxValue),
                    default=int(caps.DefaultValue),
                    writable=bool(caps.IsWritable),
                )
            )
        return out

    def set_control(self, camera_id: int, control: int, value: int) -> None:
        self._check(
            "ASISetControlValue", self._lib.ASISetControlValue(camera_id, control, value, 0)
        )

    def get_control(self, camera_id: int, control: int) -> int:
        value = ct.c_long()
        auto = ct.c_int()
        self._check(
            "ASIGetControlValue",
            self._lib.ASIGetControlValue(camera_id, control, ct.byref(value), ct.byref(auto)),
        )
        return int(value.value)

    def set_roi(self, camera_id: int, width: int, height: int, binning: int, img: int) -> None:
        self._check(
            "ASISetROIFormat", self._lib.ASISetROIFormat(camera_id, width, height, binning, img)
        )

    def start_exposure(self, camera_id: int) -> None:
        self._check("ASIStartExposure", self._lib.ASIStartExposure(camera_id, 0))

    def stop_exposure(self, camera_id: int) -> None:
        self._check("ASIStopExposure", self._lib.ASIStopExposure(camera_id))

    def exposure_status(self, camera_id: int) -> ExposureStatus:
        status = ct.c_int()
        self._check("ASIGetExpStatus", self._lib.ASIGetExpStatus(camera_id, ct.byref(status)))
        return ExposureStatus(status.value)

    def read_data(self, camera_id: int, size: int) -> bytes:
        buf = ct.create_string_buffer(size)
        self._check("ASIGetDataAfterExp", self._lib.ASIGetDataAfterExp(camera_id, buf, size))
        return buf.raw
