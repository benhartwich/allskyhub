"""The hub's meteor detection for stations (SPEC §10.3, §10.7): triplets of frames, the
buffer on disk, and tracks back in full pixels."""

from __future__ import annotations

import datetime as dt
import io
import json
import os
import time
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from allskyhub_server.devices import meteor_pairs, station_detect
from allskyhub_server.devices.station_detect import Detector, Held
from allskyhub_server.models import Device, EventRecord

DEV = "01JC3V8Q2Z7K4M9X5T6R2W8N1P"
T0 = dt.datetime(2026, 11, 5, 21, 40, tzinfo=dt.UTC)


def frame(
    width: int, height: int, streak: tuple[int, int, int, int] | None = None, seed: int = 0
) -> bytes:
    rng = np.random.default_rng(seed)
    sky = np.full((height, width), 18.0)
    stars = np.random.default_rng(7)  # the same stars in every frame
    for _ in range(300):
        x, y = int(stars.integers(0, width)), int(stars.integers(0, height))
        sky[y : y + 2, x : x + 2] = 160
    sky += rng.normal(0, 1.5, sky.shape)
    gray = np.minimum(np.maximum(sky, 0), 255).astype(np.uint8)  # not np.clip (numpy 2.5 types)
    if streak is not None:
        cv2.line(gray, streak[:2], streak[2:], 235, max(3, width // 600))
    out = io.BytesIO()
    Image.fromarray(gray).convert("RGB").save(out, "JPEG", quality=95)
    return out.getvalue()


async def _feed(detector: Detector, frames: list[bytes]) -> list[station_detect.Found | None]:
    out: list[station_detect.Found | None] = []
    for i, jpeg in enumerate(frames):
        taken = T0 + dt.timedelta(seconds=60 * i)
        out.append(await detector.feed(DEV, jpeg, taken, 30.0, "exif"))
    return out


async def test_a_streak_only_in_the_middle_frame(tmp_path: Path) -> None:
    line = (300, 300, 1100, 700)
    found = await _feed(
        Detector(tmp_path),
        [frame(1600, 1200, seed=1), frame(1600, 1200, line, seed=2), frame(1600, 1200, seed=3)],
    )
    assert found[:2] == [None, None]
    hit = found[2]
    assert hit is not None
    assert hit.held.taken == (T0 + dt.timedelta(seconds=60)).timestamp()  # the middle one
    assert hit.jpeg[:2] == b"\xff\xd8"
    assert len(hit.meteors) == 1
    track, peak = hit.meteors[0]
    ends = sorted([track[0], track[-1]])
    assert ends[0] == pytest.approx((300, 300), abs=25)
    assert ends[1] == pytest.approx((1100, 700), abs=25)
    assert 0 < peak <= 1


async def test_no_meteor_without_a_fresh_streak(tmp_path: Path) -> None:
    line = (300, 300, 1100, 700)
    everywhere = await _feed(
        Detector(tmp_path / "a"), [frame(1600, 1200, line, seed=s) for s in (1, 2, 3)]
    )
    assert everywhere == [None, None, None]
    sizes = await _feed(
        Detector(tmp_path / "b"),
        [frame(1600, 1200, seed=1), frame(1600, 1200, line, seed=2), frame(1500, 1200, seed=3)],
    )
    assert sizes == [None, None, None]
    small = await _feed(
        Detector(tmp_path / "c"),
        [frame(1000, 750, seed=1), frame(1000, 750, (100, 100, 700, 400), seed=2),
         frame(1000, 750, seed=3)],
    )  # fmt: skip
    assert small == [None, None, None]  # under 1280 px: brightness and clouds only


async def test_tracks_back_in_full_pixels(tmp_path: Path) -> None:
    """A 4000 px frame is looked at in 2048 px; the track comes back in the frame's."""
    line = (1000, 800, 2600, 1700)
    found = await _feed(
        Detector(tmp_path),
        [frame(4000, 3000, seed=1), frame(4000, 3000, line, seed=2), frame(4000, 3000, seed=3)],
    )
    hit = found[2]
    assert hit is not None
    assert (hit.held.width, hit.held.gray_w) == (4000, 2048)
    track, _ = hit.meteors[0]
    ends = sorted([track[0], track[-1]])
    assert ends[0] == pytest.approx((1000, 800), abs=40)
    assert ends[1] == pytest.approx((2600, 1700), abs=40)


async def test_the_buffer_on_disk(tmp_path: Path) -> None:
    detector = Detector(tmp_path)
    await _feed(detector, [frame(1600, 1200, seed=s) for s in (1, 2, 3)])
    folder = tmp_path / DEV
    ring = json.loads((folder / "ring.json").read_text())
    assert len(ring) == 2
    assert len(list(folder.glob("*.npy"))) == 2
    assert len(list(folder.glob("*.jpg"))) == 1  # only the newest can be a middle frame
    # A second detector (the hub restarted without cleaning) carries on from the disk.
    again = Detector(tmp_path)
    line = (300, 300, 1100, 700)
    later = T0 + dt.timedelta(minutes=3)
    assert await again.feed(DEV, frame(1600, 1200, line, seed=4), later, 30.0, "exif") is None
    hit = await again.feed(
        DEV, frame(1600, 1200, seed=5), later + dt.timedelta(minutes=1), 30.0, "exif"
    )
    assert hit is not None
    # Silent for an hour: the folder goes; at the start everything goes.
    old = time.time() - station_detect.STALE_S - 10
    os.utime(folder / "ring.json", (old, old))
    assert again.buffer.clean() == 1
    assert not folder.exists()
    await _feed(again, [frame(1600, 1200, seed=1)])
    assert again.buffer.clean(everything=True) == 1


async def test_a_spoiled_buffer_starts_anew(tmp_path: Path) -> None:
    detector = Detector(tmp_path)
    await _feed(detector, [frame(1600, 1200, seed=s) for s in (1, 2)])
    folder = tmp_path / DEV
    for path in folder.glob("*.npy"):
        np.save(path, np.zeros((5, 5), dtype=np.float64))  # pyright: ignore[reportUnknownMemberType]
    held = detector.buffer.ring(DEV)[0]
    assert detector.buffer.gray(DEV, held) is None
    assert not folder.exists()
    with pytest.raises(ValueError, match="etc"):
        detector.buffer.folder("../../etc")


def test_runs() -> None:
    def held(t: float, w: int = 1600) -> Held:
        return Held("x", t, 30.0, "exif", w, 1200, w, 1200)

    assert station_detect.in_run(held(0), held(60), held(120), None)
    assert not station_detect.in_run(held(0), held(60), held(300), None)  # a gap
    assert station_detect.in_run(held(0), held(60), held(150), 60.0)
    assert not station_detect.in_run(held(0), held(60), held(200), 60.0)
    assert not station_detect.in_run(held(0), held(60), held(120, 1500), None)
    assert not station_detect.in_run(held(0, 1000), held(60, 1000), held(120, 1000), None)


def test_trial_meteors_are_not_paired() -> None:
    device = Device(id=DEV, last_status={"settings": {"latitude": 48.3, "longitude": 4.29}})
    data = {"start_azimuth_deg": 10.0, "start_altitude_deg": 40.0,
            "end_azimuth_deg": 20.0, "end_altitude_deg": 30.0}  # fmt: skip
    assert meteor_pairs.sighting(device, EventRecord(data=data)) is not None
    assert meteor_pairs.sighting(device, EventRecord(data=data | {"trial": True})) is None
