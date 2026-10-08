"""Lightning detection (roadmap #5, SPEC §6.4) on synthetic night frames."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from allskyhub_agent.detect.events import DetectionWorker, EventStore
from allskyhub_agent.detect.lightning import LightningDetector, LightningHit
from allskyhub_agent.store.images import ImageStore
from allskyhub_protocol import Event, EventKind, FrameInfo, Mode

H, W = 360, 640
T0 = datetime(2026, 7, 15, 21, 0, tzinfo=UTC)


def sky(seed: int, level: float = 30.0) -> np.ndarray:
    r = np.random.default_rng(seed)
    img = r.normal(level, 2.0, (H, W)).astype(np.float32)
    return np.minimum(np.maximum(img, 0), 255).astype(np.uint8)


def flash(img: np.ndarray, y0: int = 200, y1: int = 300, value: int = 140) -> np.ndarray:
    out = img.copy()
    out[y0:y1, 50:600] = value  # clouds near the horizon lit up
    return out


def frame(i: int, sun: float = -30.0, exposure_us: int = 30_000_000, minutes: int = 1) -> FrameInfo:
    t = T0 + timedelta(minutes=i * minutes)
    return FrameInfo(
        captured_at=t,
        night_id="20260715",
        name=f"image-{t:%Y%m%d%H%M%S}.jpg",
        mode=Mode.NIGHT,
        exposure_us=exposure_us,
        gain=100,
        mean=0.12,
        sun_elevation=sun,
        profile="sim",
    )


def run(frames: list[np.ndarray], infos: list[FrameInfo] | None = None) -> list[LightningHit]:
    det = LightningDetector()
    hits: list[LightningHit] = []
    for i, img in enumerate(frames):
        f = infos[i] if infos else frame(i)
        hits += det.feed(f, img, Path(f"/x/{i}.jpg"))
    return hits + det.flush()


def test_flash_in_one_frame_is_detected() -> None:
    hits = run([sky(1), flash(sky(2)), sky(3)])
    assert len(hits) == 1
    h = hits[0]
    assert h.frame.name == frame(1).name
    assert 0.2 < h.flash.area_frac < 0.3
    assert h.flash.peak > 0.3
    assert h.storm_flashes == 1
    assert h.storm_start == frame(1).captured_at


def test_cloud_that_drifts_in_and_stays_is_not_a_flash() -> None:
    assert run([sky(1), flash(sky(2), value=80), flash(sky(3), value=80), sky(4)]) == []


def test_darker_frame_after_the_flash_still_confirms() -> None:
    # Auto exposure reacts to the flash: the next frame is darker overall.
    infos = [frame(0), frame(1), frame(2, exposure_us=15_000_000)]
    assert len(run([sky(1), flash(sky(2)), sky(3, level=15)], infos)) == 1


def test_small_brightening_and_global_offset_are_ignored() -> None:
    meteor_sized = sky(2)
    meteor_sized[150:153, 100:300] = 230
    assert run([sky(1), meteor_sized, sky(3)]) == []
    assert run([sky(1), sky(2, level=60), sky(3)]) == []  # the moon rising: the whole sky


def test_exposure_step_is_not_compared() -> None:
    infos = [frame(0), frame(1, exposure_us=10_000_000), frame(2, exposure_us=10_000_000)]
    assert run([sky(1), flash(sky(2)), sky(3)], infos) == []


def test_dusk_is_skipped() -> None:
    infos = [frame(i, sun=-8.0) for i in range(3)]
    assert run([sky(1), flash(sky(2)), sky(3)], infos) == []


def test_storm_counts_and_groups_flashes() -> None:
    frames = [sky(0)]
    for k in range(1, 7):
        frames.append(flash(sky(k)) if k % 2 else sky(k))
    hits = run(frames)
    assert [h.storm_flashes for h in hits] == [1, 2, 3]
    assert {h.storm_start for h in hits} == {frame(1).captured_at}
    # A gap longer than the storm window starts a new storm.
    later = run([sky(0), flash(sky(1)), sky(2), flash(sky(3)), sky(4)],
                [frame(i, minutes=40) for i in range(5)])  # fmt: skip
    assert [h.storm_start for h in later] == [frame(1, minutes=40).captured_at,
                                              frame(3, minutes=40).captured_at]  # fmt: skip
    assert [h.storm_flashes for h in later] == [1, 1]


def test_lightning_event(tmp_path: Path) -> None:
    store = ImageStore(tmp_path, ZoneInfo("Europe/Vienna"))
    got: list[Event] = []
    worker = DetectionWorker(store, on_event=got.append)
    for i, gray in enumerate([sky(1), flash(sky(2)), sky(3)]):
        rgb = np.empty((H, W, 3), dtype=np.uint8)
        rgb[:, :, :] = gray[:, :, None]
        stored = store.save(rgb, frame(i).captured_at)
        f = frame(i).model_copy(update={"name": stored.name, "night_id": stored.night_id})
        worker.process_frame(f, rgb)
    assert [e.kind for e in got] == [EventKind.LIGHTNING]
    ev = got[0]
    assert ev.id == "lightning-20260715T210100Z"
    assert ev.has_image
    assert set(ev.data) == {"area_frac", "peak", "storm_flashes", "storm"}
    assert ev.data["storm"] == "storm-20260715T210100Z"
    events = EventStore(store)
    assert events.night_events(ev.night_id) == [ev]
    full = events.image_path(ev.night_id, ev.id, thumbnail=False)
    assert full is not None
    assert full.read_bytes()[:2] == b"\xff\xd8"
