"""Raspberry Pi camera adapter against a fake rpicam-still (no hardware needed)."""

from __future__ import annotations

import json
import stat
import sys
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from allskyhub_agent.adapters.camera import CameraError, CaptureRequest, Frame
from allskyhub_agent.adapters.libcamera import (
    LibcameraCamera,
    LibcameraSettings,
    parse_list_cameras,
)
from allskyhub_agent.core.clock import SimClock
from allskyhub_agent.core.exposure import AutoExposure
from allskyhub_agent.profiles import get_profile
from allskyhub_agent.runner import Location, Runner
from allskyhub_agent.store.images import ImageStore

# Writes a small PNG and metadata like rpicam-still; behaviour comes from env variables:
# FAKE_MODE=ok|fail|nooutput|sleep, FAKE_GAIN (reported AnalogueGain), FAKE_ARGS (log file).
FAKE = r"""#!PYTHON
import json, os, sys, time
from PIL import Image
args = sys.argv[1:]
with open(os.environ["FAKE_ARGS"], "a") as f:
    f.write(json.dumps(args) + "\n")
mode = os.environ.get("FAKE_MODE", "ok")
if mode == "fail":
    print("ERROR: *** no cameras available ***", file=sys.stderr)
    sys.exit(255)
if mode == "sleep":
    time.sleep(5)
def arg(name):
    return args[args.index(name) + 1]
if mode != "nooutput":
    Image.new("RGB", (64, 48), (200, 100, 10)).save(arg("--output"), format="PNG")
json.dump({"ExposureTime": int(arg("--shutter")) // 2,
           "AnalogueGain": float(os.environ.get("FAKE_GAIN", arg("--gain"))),
           "SensorTemperature": 41.25}, open(arg("--metadata"), "w"))
"""


@pytest.fixture
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    exe = tmp_path / "rpicam-still"
    exe.write_text(FAKE.replace("PYTHON", sys.executable))
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    log = tmp_path / "args.jsonl"
    monkeypatch.setenv("FAKE_ARGS", str(log))
    monkeypatch.setenv("FAKE_MODE", "ok")
    return exe


def _args(log: Path) -> list[list[str]]:
    return [json.loads(line) for line in log.read_text().splitlines()]


def test_capture_reads_image_and_metadata(fake: Path, tmp_path: Path) -> None:
    cam = LibcameraCamera(
        LibcameraSettings(executable=str(fake), awb_gains=(1.6, 1.9), width=64, height=48)
    )
    frame = cam.capture(CaptureRequest(exposure_us=2_000_000, gain=200.0))
    assert frame.image.shape == (48, 64, 3)
    assert frame.image[0, 0].tolist() == [200, 100, 10]
    # The values the camera reports win over the requested ones.
    assert frame.exposure_us == 1_000_000
    assert frame.gain == pytest.approx(200.0, abs=0.1)  # 10x linear = 20 dB = 200 units
    assert frame.sensor_temp_c == 41.2
    args = _args(tmp_path / "args.jsonl")[0]
    assert args[args.index("--shutter") + 1] == "2000000"
    assert args[args.index("--gain") + 1] == "10.000"
    assert args[args.index("--awbgains") + 1] == "1.600,1.900"
    assert "--immediate" in args
    assert "--nopreview" in args
    cam.close()


def test_clamped_gain_is_reported(fake: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_GAIN", "16.0")  # sensor maximum below the request
    frame = LibcameraCamera(LibcameraSettings(executable=str(fake))).capture(
        CaptureRequest(1000, 260.0)
    )
    assert frame.gain == pytest.approx(240.8, abs=0.1)


@pytest.mark.parametrize(("mode", "match"), [("fail", "no cameras"), ("nooutput", "failed")])
def test_failures_raise_camera_error(
    fake: Path, monkeypatch: pytest.MonkeyPatch, mode: str, match: str
) -> None:
    monkeypatch.setenv("FAKE_MODE", mode)
    cam = LibcameraCamera(LibcameraSettings(executable=str(fake)))
    with pytest.raises(CameraError, match=match):
        cam.capture(CaptureRequest(1000, 0))


def test_timeout_raises_camera_error(fake: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_MODE", "sleep")
    cam = LibcameraCamera(LibcameraSettings(executable=str(fake), timeout_extra_s=0.5))
    with pytest.raises(CameraError, match="did not finish"):
        cam.capture(CaptureRequest(1000, 0))


def test_missing_executable() -> None:
    with pytest.raises(CameraError, match="not found"):
        LibcameraCamera(LibcameraSettings(executable="no-such-rpicam-still"))


def test_parse_list_cameras() -> None:
    out = (
        "Available cameras\n-----------------\n"
        "0 : imx477 [4056x3040 12-bit RGGB] (/base/soc/i2c0mux/i2c@1/imx477@1a)\n"
        "    Modes: 'SRGGB10_CSI2P' : 1332x990 [120.05 fps - (696, 528)/2664x1980 crop]\n"
        "1 : imx708_wide [4608x2592 10-bit RGGB] (/base/axi/pcie@120000/rp1/i2c@88000/imx708@1a)\n"
    )
    cams = parse_list_cameras(out)
    assert [(c.index, c.sensor, c.width, c.height) for c in cams] == [
        (0, "imx477", 4056, 3040),
        (1, "imx708_wide", 4608, 2592),
    ]
    assert parse_list_cameras("No cameras available!") == []


class FlakyCamera:
    """Fails twice, then works: the runner must survive and keep going."""

    def __init__(self) -> None:
        self.calls = 0

    @property
    def name(self) -> str:
        return "flaky"

    def capture(self, req: CaptureRequest) -> Frame:
        self.calls += 1
        if self.calls <= 2:
            raise CameraError("USB reset")
        return Frame(np.zeros((9, 16, 3), dtype=np.uint8), req.exposure_us, req.gain)

    def close(self) -> None:
        return None


def test_runner_survives_camera_errors(tmp_path: Path) -> None:
    clock = SimClock(datetime(2026, 10, 6, 21, 0, tzinfo=UTC))
    cam = FlakyCamera()
    runner = Runner(
        camera=cam,
        auto_exposure=AutoExposure(get_profile("sim").exposure),
        store=ImageStore(tmp_path, ZoneInfo("Europe/Vienna")),
        clock=clock,
        location=Location(48.14, 14.39),
        profile="sim",
    )
    start = clock.now()
    assert runner.run(2) == 2
    assert cam.calls == 4
    # Waited 5 s, then 10 s, before the camera came back.
    assert (clock.now() - start).total_seconds() >= 15
    assert list((tmp_path / "images").iterdir())
