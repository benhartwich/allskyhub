"""Moon HDR test (experimental, setting `moon_hdr_test`, default off).

While a bright moon is up at night, every `interval_s` the long frame is kept as it came
from the camera, together with a few short exposures at the lowest gain, as PNG and, where
the camera can, as DNG. Nothing is merged: the sets are for trying the merge of SPEC §4.13
(draft) offline. The stored frames, measurements and detections stay those of the long
frame alone.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import shutil
from collections.abc import Sequence
from pathlib import Path

from PIL import Image as PILImage

from allskyhub_agent.adapters.camera import Camera, CameraError, CaptureRequest, Frame

log = logging.getLogger(__name__)

# Each set is about 4 DNGs of 18 MB (HQ camera); below this much free space it stops.
MIN_FREE_BYTES = 5 * 1024**3


class MoonHdrTest:
    def __init__(
        self,
        root: Path,
        interval_s: float = 600.0,
        short_us: Sequence[int] = (250, 1000, 4000),
        short_gain: float = 0.0,
    ) -> None:
        self._root = root
        self._interval = dt.timedelta(seconds=interval_s)
        self._short_us = tuple(short_us)
        self._short_gain = short_gain
        self._last: dt.datetime | None = None

    def due(self, now: dt.datetime, night_id: str) -> Path | None:
        """The folder for a new set, or None when the last one is too recent or the disk
        is getting full."""
        if self._last is not None and now - self._last < self._interval:
            return None
        self._root.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(self._root).free < MIN_FREE_BYTES:
            return None
        self._last = now
        folder = self._root / night_id / now.strftime("%Y%m%d-%H%M%S")
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def record(
        self,
        camera: Camera,
        folder: Path,
        long: Frame,
        captured_at: dt.datetime,
        moon_altitude: float,
        moon_illumination: float,
    ) -> None:
        """Keep the long frame and take the short ones right after it. A failed short
        exposure is logged and skipped; it never stops the capture loop."""
        _png(long, folder / "long.png")
        shorts: list[dict[str, object]] = []
        for us in self._short_us:
            name = f"short-{us}us"
            try:
                frame = camera.capture(
                    CaptureRequest(us, self._short_gain, raw=folder / f"{name}.dng")
                )
            except CameraError as exc:
                log.warning("moon HDR test: %s failed: %s", name, exc)
                continue
            _png(frame, folder / f"{name}.png")
            shorts.append({"file": name, **_exposure(frame)})
        meta = {
            "captured_at": captured_at.isoformat(),
            "long": {"file": "long", **_exposure(long)},
            "short": shorts,
            "moon": {
                "altitude_deg": round(moon_altitude, 1),
                "illumination": round(moon_illumination, 2),
            },
        }
        (folder / "set.json").write_text(json.dumps(meta, indent=1))


def _png(frame: Frame, path: Path) -> None:
    PILImage.fromarray(frame.image).save(path, compress_level=1)


def _exposure(frame: Frame) -> dict[str, object]:
    return {
        "exposure_us": frame.exposure_us,
        "gain": frame.gain,
        "sensor_temp_c": frame.sensor_temp_c,
    }
