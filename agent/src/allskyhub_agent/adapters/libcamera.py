"""Raspberry Pi cameras through `rpicam-still` (SPEC §3, profile `rpi-hq`).

One `rpicam-still` run per frame with fixed exposure and gain, as Allsky does for these
cameras; it is more robust than the Python bindings, which only exist as a system package.
The frame reports the exposure and gain the camera actually used (from the metadata), so
the exposure controller gets truthful feedback when the sensor clamps a value.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image as PILImage

from allskyhub_agent.adapters.camera import CameraError, CaptureRequest, Frame, Image


@dataclass(frozen=True)
class LibcameraSettings:
    executable: str = "rpicam-still"
    camera: int = 0
    width: int = 0  # 0: the sensor's full resolution
    height: int = 0
    # Fixed white balance (red, blue gains); None leaves AWB on, which drifts at night.
    awb_gains: tuple[float, float] | None = None
    denoise: str = "cdn_off"
    # Gain in the agent's units of 0.1 dB, as for ZWO (profile, SPEC §3).
    gain_db_per_unit: float = 0.1
    # rpicam-still needs a few frames at the requested shutter: allow 3 exposures + slack.
    timeout_factor: float = 3.0
    timeout_extra_s: float = 30.0


@dataclass(frozen=True)
class CameraListing:
    index: int
    sensor: str
    width: int
    height: int


_LIST_LINE = re.compile(r"^\s*(\d+)\s*:\s*(\S+)\s*\[(\d+)x(\d+)")


def parse_list_cameras(output: str) -> list[CameraListing]:
    """Parse `rpicam-still --list-cameras`, e.g. `0 : imx477 [4056x3040 12-bit RGGB] (...)`."""
    cams: list[CameraListing] = []
    for line in output.splitlines():
        m = _LIST_LINE.match(line)
        if m:
            cams.append(CameraListing(int(m[1]), m[2], int(m[3]), int(m[4])))
    return cams


def list_cameras(executable: str = "rpicam-still") -> list[CameraListing]:
    exe = shutil.which(executable)
    if exe is None:
        return []
    res = subprocess.run(  # noqa: S603 - fixed binary, no shell
        [exe, "--list-cameras"], capture_output=True, text=True, check=False, timeout=30
    )
    return parse_list_cameras(res.stdout + res.stderr)


class LibcameraCamera:
    """`Camera` implementation for Raspberry Pi cameras (HQ, Camera Module 3, ...)."""

    def __init__(self, settings: LibcameraSettings | None = None) -> None:
        self._cfg = settings or LibcameraSettings()
        exe = shutil.which(self._cfg.executable)
        if exe is None:
            raise CameraError(f"{self._cfg.executable} not found")
        self._exe = exe
        self._tmp = Path(tempfile.mkdtemp(prefix="allskyhub-cam-"))
        self._name = f"libcamera {self._cfg.camera}"

    @property
    def name(self) -> str:
        return self._name

    def _units_to_linear(self, gain: float) -> float:
        return max(1.0, 10 ** (gain * self._cfg.gain_db_per_unit / 20.0))

    def _linear_to_units(self, linear: float) -> float:
        return round(20.0 * math.log10(max(linear, 1.0)) / self._cfg.gain_db_per_unit, 1)

    def command(self, req: CaptureRequest, out: Path, meta: Path) -> list[str]:
        c = self._cfg
        cmd = [
            self._exe, "--camera", str(c.camera), "--nopreview", "--immediate",
            "--shutter", str(max(1, req.exposure_us)),
            "--gain", f"{self._units_to_linear(req.gain):.3f}",
            "--denoise", c.denoise, "--encoding", "png",
            "--metadata", str(meta), "--metadata-format", "json",
            "--output", str(out),
        ]  # fmt: skip
        if c.width and c.height:
            cmd += ["--width", str(c.width), "--height", str(c.height)]
        if c.awb_gains is not None:
            cmd += ["--awbgains", f"{c.awb_gains[0]:.3f},{c.awb_gains[1]:.3f}"]
        return cmd

    def capture(self, req: CaptureRequest) -> Frame:
        out, meta = self._tmp / "frame.png", self._tmp / "meta.json"
        out.unlink(missing_ok=True)
        meta.unlink(missing_ok=True)
        timeout = req.exposure_us / 1e6 * self._cfg.timeout_factor + self._cfg.timeout_extra_s
        try:
            res = subprocess.run(  # noqa: S603 - fixed binary, no shell
                self.command(req, out, meta),
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise CameraError(f"rpicam-still did not finish within {timeout:.0f} s") from exc
        if res.returncode != 0 or not out.is_file():
            tail = (res.stderr or res.stdout).strip().splitlines()[-3:]
            raise CameraError(f"rpicam-still failed ({res.returncode}): {' | '.join(tail)}")

        with PILImage.open(out) as im:
            rgb8 = im.convert("RGB")
            img: Image = np.asarray(rgb8, dtype=np.uint8)

        exposure_us, gain, temp = req.exposure_us, req.gain, None
        try:
            md: object = json.loads(meta.read_text())
        except (OSError, ValueError):
            md = None
        if isinstance(md, dict):
            data: dict[str, object] = md  # pyright: ignore[reportUnknownVariableType]
            et, ag, st = (
                data.get("ExposureTime"),
                data.get("AnalogueGain"),
                data.get("SensorTemperature"),
            )
            if isinstance(et, int | float) and et > 0:
                exposure_us = int(et)
            if isinstance(ag, int | float) and ag > 0:
                gain = self._linear_to_units(float(ag))
            if isinstance(st, int | float):
                temp = round(float(st), 1)
        return Frame(image=img, exposure_us=exposure_us, gain=gain, sensor_temp_c=temp)

    def close(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)
