"""Aurora detection (roadmap #5, SPEC §6.4) on synthetic night frames."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import cv2
import numpy as np

from allskyhub_agent.detect.aurora import (
    AuroraConfig,
    AuroraDetector,
    AuroraUpdate,
    ring_mask,
    score,
)
from allskyhub_agent.detect.events import DetectionWorker, EventStore
from allskyhub_agent.store.images import ImageStore
from allskyhub_protocol import Event, EventKind, FrameInfo, Mode

H, W = 480, 640
T0 = datetime(2026, 10, 8, 21, 0, tzinfo=UTC)
CFG = AuroraConfig()
BAND = ring_mask(H, W, 0.48, CFG)


def sky(seed: int, glow: tuple[int, int, int] | None = None) -> np.ndarray:
    """A night frame; `glow` paints structured patches low on the right (east in the image)."""
    r = np.random.default_rng(seed)
    base = r.normal(35, 2.0, (H, W)).astype(np.float32)
    rgb = np.empty((H, W, 3), dtype=np.uint8)
    rgb[:, :, :] = np.minimum(np.maximum(base, 0), 255).astype(np.uint8)[:, :, None]
    if glow is not None:
        for k in range(4):  # rays: separate bright bands, not one smooth area
            cv2.rectangle(rgb, (430 + 22 * k, 170), (442 + 22 * k, 300), glow, -1)
    return rgb


GREEN = (40, 120, 50)
RED_CLOUD = (150, 90, 60)  # moonlit / sodium-lit cloud: bright, but red-dominant


def frame(i: int, sun: float = -30.0, minutes: int = 2) -> FrameInfo:
    t = T0 + timedelta(minutes=i * minutes)
    return FrameInfo(
        captured_at=t,
        night_id="20261008",
        name=f"image-{t:%Y%m%d%H%M%S}.jpg",
        mode=Mode.NIGHT,
        exposure_us=30_000_000,
        gain=100,
        mean=0.1,
        sun_elevation=sun,
        profile="sim",
    )


def test_green_glow_scores_and_red_cloud_does_not() -> None:
    s = score(sky(1, GREEN), BAND, CFG)
    assert s is not None
    assert s.candidate
    assert s.index > CFG.min_index
    assert s.green > 30
    assert s.direction_deg is not None
    assert 60 < s.direction_deg < 120  # right of the centre: "east" in the image
    for img in (sky(1), sky(1, RED_CLOUD)):
        quiet = score(img, BAND, CFG)
        assert quiet is not None
        assert not quiet.candidate


def run(
    frames: list[tuple[FrameInfo, np.ndarray]], cloud: float | None = None
) -> list[AuroraUpdate]:
    det = AuroraDetector(CFG)
    out: list[AuroraUpdate] = []
    for f, img in frames:
        out += det.feed(f, img, Path(f"/x/{f.name}"), cloud)
    return out + det.flush()


def test_episode_opens_after_two_frames_resends_and_closes() -> None:
    frames = [(frame(0), sky(0))]
    frames += [(frame(i), sky(i, GREEN)) for i in range(1, 9)]  # 16 min of aurora
    ups = run(frames)
    assert ups[0].episode.start.captured_at == frame(1).captured_at
    assert ups[0].episode.ongoing
    assert ups[0].picture_changed
    # Opened at frame 2, resent every 5 min (frames 5 and 8), closed by flush.
    assert len(ups) == 4
    assert [u.episode.ongoing for u in ups] == [True, True, True, False]
    assert ups[-1].episode.frames == 8


def test_single_frame_does_not_open() -> None:
    assert run([(frame(0), sky(0)), (frame(1), sky(1, GREEN)), (frame(2), sky(2))]) == []


def test_gap_closes_and_twilight_and_overcast_are_skipped() -> None:
    frames = [(frame(i), sky(i, GREEN)) for i in range(3)]
    frames.append((frame(30), sky(30, GREEN)))  # 60 min later: a new episode
    frames.append((frame(31), sky(31, GREEN)))
    ups = run(frames)
    starts = {u.episode.start.captured_at for u in ups}
    assert starts == {frame(0).captured_at, frame(30).captured_at}
    dusk = [(frame(i, sun=-10), sky(i, GREEN)) for i in range(4)]
    assert run(dusk) == []
    assert run([(frame(i), sky(i, GREEN)) for i in range(4)], cloud=0.95) == []


def test_aurora_events_upsert_by_id(tmp_path: Path) -> None:
    store = ImageStore(tmp_path, ZoneInfo("Europe/Vienna"))
    got: list[Event] = []
    worker = DetectionWorker(store, on_event=got.append, mask_radius_frac=0.48)
    imgs = [sky(0)] + [sky(i, GREEN) for i in range(1, 5)]
    for i, rgb in enumerate(imgs):
        stored = store.save(rgb, frame(i).captured_at)
        f = frame(i).model_copy(update={"name": stored.name, "night_id": stored.night_id})
        worker.process_frame(f, rgb)
    aurora = [e for e in got if e.kind is EventKind.AURORA]
    assert aurora
    first = aurora[0]
    assert first.id.startswith("aurora-")
    assert first.has_image
    assert first.data["image_rev"] == 1
    assert first.data["ongoing"] is True
    assert set(first.data) == {"peak_index", "green", "frames", "direction_deg", "ongoing",
                               "image_rev", "azimuth_deg", "altitude_deg"}  # fmt: skip
    events = EventStore(store)
    stored_events = [e for e in events.night_events(first.night_id) if e.kind is EventKind.AURORA]
    assert [e.id for e in stored_events] == [first.id]  # one entry, the last update
    assert stored_events[0] == aurora[-1]
    assert events.image_path(first.night_id, first.id, thumbnail=True) is not None


def test_restart_closes_an_open_episode(tmp_path: Path) -> None:
    store = ImageStore(tmp_path, ZoneInfo("UTC"))
    for run_no in range(2):  # two agent runs, each sees an aurora start
        worker = DetectionWorker(store, mask_radius_frac=0.48)
        for i in range(3):
            k = 10 * run_no + i
            rgb = sky(k, GREEN)
            stored = store.save(rgb, frame(k).captured_at)
            upd = {"name": stored.name, "night_id": stored.night_id}
            worker.process_frame(frame(k).model_copy(update=upd), rgb)
    events = [e for e in EventStore(store).night_events("20261008") if e.kind is EventKind.AURORA]
    assert len(events) == 2
    assert [e.data["ongoing"] for e in events] == [False, True]


def test_with_an_orientation_only_the_polar_sector_counts() -> None:
    from allskyhub_agent.calib.solve import Solution

    sol = Solution(cx=W / 2, cy=H / 2, a1=230.0, a3=0.0, rot=0.0, flip=1.0, tilt_east=0.0,
                   tilt_north=0.0, stars=30, rms_px=1.0, rms_deg=0.1,
                   width=W, height=H)  # fmt: skip
    east = [(frame(i), sky(i, GREEN)) for i in range(3)]  # the rays are right of the centre
    north_up = AuroraDetector(CFG, orientation=lambda: sol, latitude=lambda: 48.1)
    assert [u for f, img in east for u in north_up.feed(f, img, Path(f"/{f.name}"))] == []
    # The same rays with north to the right of the image: now they are toward the pole.
    sol_e = dataclasses.replace(sol, rot=90.0)
    north_right = AuroraDetector(CFG, orientation=lambda: sol_e, latitude=lambda: 48.1)
    assert [u for f, img in east for u in north_right.feed(f, img, Path(f"/{f.name}"))]
    # Southern hemisphere: the pole is south, opposite.
    south = AuroraDetector(CFG, orientation=lambda: sol_e, latitude=lambda: -45.0)
    assert [u for f, img in east for u in south.feed(f, img, Path(f"/{f.name}"))] == []
