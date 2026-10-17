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
    assert set(ev.data) == {"area_frac", "peak", "storm_flashes", "storm", "azimuth_deg",
                            "altitude_deg", "frame", "crop_x", "crop_y", "crop_w",
                            "crop_h"}  # fmt: skip
    assert ev.data["crop_w"] is None  # SPEC §6.4: the picture is the whole frame
    assert ev.data["storm"] == "storm-20260715T210100Z"
    events = EventStore(store)
    assert events.night_events(ev.night_id) == [ev]
    full = events.image_path(ev.night_id, ev.id, thumbnail=False)
    assert full is not None
    assert full.read_bytes()[:2] == b"\xff\xd8"


# --- SPEC §6.4 Light source check (roadmap L35) ----------------------------------------------


def rgb_sky(seed: int) -> np.ndarray:
    g = sky(seed)
    out = np.empty((H, W, 3), dtype=np.uint8)
    out[:, :, :] = g[:, :, None]
    return out


def rgb_flash(seed: int, colour: tuple[int, int, int], x0: int = 50, x1: int = 600) -> np.ndarray:
    out = rgb_sky(seed)
    out[200:300, x0:x1] = colour
    return out


def gray_of(rgb: np.ndarray) -> np.ndarray:
    return rgb.mean(axis=-1).astype(np.uint8)


def run_rgb(frames: list[np.ndarray], mode: str = "label") -> list[LightningHit]:
    from allskyhub_agent.detect.lightning import LightningConfig

    det = LightningDetector(LightningConfig(light_source=mode))
    hits: list[LightningHit] = []
    for i, img in enumerate(frames):
        f = frame(i)
        hits += det.feed(f, gray_of(img), Path(f"/x/{i}.jpg"), img)
    return hits


def lamp_night(
    colour: tuple[int, int, int], second_x: tuple[int, int] = (50, 600)
) -> list[np.ndarray]:
    return [rgb_sky(1), rgb_flash(2, colour), rgb_sky(3), rgb_sky(4),
            rgb_flash(5, colour, *second_x), rgb_sky(6), rgb_sky(7)]  # fmt: skip


def test_a_lamp_lighting_the_same_place_twice_is_a_light_source() -> None:
    hits = run_rgb(lamp_night((200, 60, 160)))
    assert [h.light_source for h in hits] == [False, True]
    assert hits[1].also == (hits[0].seq,)  # the first one is the lamp too
    assert hits[1].storm_flashes == 2  # "label" still counts it


def test_bluish_white_flashes_at_the_same_place_stay_lightning() -> None:
    # A distant storm lights the same sector again and again.
    hits = run_rgb(lamp_night((160, 170, 200)))
    assert [h.light_source for h in hits] == [False, False]


def test_lamp_coloured_flashes_at_different_places_stay_lightning() -> None:
    night = lamp_night((200, 60, 160), second_x=(50, 120))
    night[1] = rgb_flash(2, (200, 60, 160), 500, 600)
    hits = run_rgb(night)
    assert [h.light_source for h in hits] == [False, False]


def test_drop_leaves_the_lamp_out_of_the_storm() -> None:
    hits = run_rgb(lamp_night((200, 60, 160)), mode="drop")
    assert len(hits) == 1
    assert hits[0].storm_flashes == 1
    assert hits[0].light_source is False


def test_off_does_not_check() -> None:
    hits = run_rgb(lamp_night((200, 60, 160)), mode="off")
    assert [h.light_source for h in hits] == [False, False]


def test_worker_reports_the_lamp_before_and_after(tmp_path: Path) -> None:
    # The second flash is labelled before it goes out, the first one after (SPEC §6.4).
    store = ImageStore(tmp_path, ZoneInfo("Europe/Vienna"))
    order: list[str] = []
    worker = DetectionWorker(
        store,
        on_event=lambda e: order.append(f"event {e.id}"),
        on_light_source=lambda e, sent: order.append(f"lamp {e.id} {sent}"),
    )
    for i, rgb in enumerate(lamp_night((200, 60, 160))):
        stored = store.save(rgb, frame(i).captured_at)
        f = frame(i).model_copy(update={"name": stored.name, "night_id": stored.night_id})
        worker.process_frame(f, rgb)
    first, second = "lightning-20260715T210100Z", "lightning-20260715T210400Z"
    assert order == [f"event {first}", f"lamp {second} False", f"event {second}",
                     f"lamp {first} True"]  # fmt: skip
