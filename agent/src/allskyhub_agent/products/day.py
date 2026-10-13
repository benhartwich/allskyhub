"""Day products and the mini timelapse (SPEC §5.2, roadmap L21).

Day products are built at dusk from the good day frames of that local date. The morning's
frames sit in the previous night id's folder (before noon, SPEC §4.5), the afternoon's in
the current one; the files go into the current one. They stay local: the `products`
message lists only the night products until the hub accepts more kinds.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta, tzinfo
from pathlib import Path
from typing import cast

from allskyhub_agent.products.build import (
    ProductConfig,
    keogram,
    save_jpeg,
    thumbnail,
    timelapse,
)
from allskyhub_agent.store.images import ImageStore
from allskyhub_protocol import FrameInfo, Mode

log = logging.getLogger(__name__)

DAY_TIMELAPSE = "day-timelapse.mp4"
DAY_KEOGRAM = "day-keogram.jpg"
DAY_MANIFEST = "day-products.json"
DAY_NAMES = {DAY_TIMELAPSE: "video/mp4", DAY_KEOGRAM: "image/jpeg"}
MINI = "mini.mp4"
MINI_WINDOW = timedelta(hours=1)
MINI_EVERY_S = 600.0
MINI_MAX_WIDTH = 640


@dataclass(frozen=True)
class DayConfig:
    timelapse: bool = True
    keogram: bool = False


@dataclass
class DayProducts:
    night_id: str
    frames: int = 0
    built: list[str] = field(default_factory=list[str])
    skipped: dict[str, str] = field(default_factory=dict[str, str])


def _night_before(night: str) -> str:
    d = datetime.strptime(night, "%Y%m%d").date() - timedelta(days=1)  # noqa: DTZ007
    return d.strftime("%Y%m%d")


def day_frames(store: ImageStore, night: str, tz: tzinfo) -> list[FrameInfo]:
    """The good day frames of the local date `night` (YYYYMMDD), in capture order."""
    day = date(int(night[:4]), int(night[4:6]), int(night[6:]))
    out = [
        f
        for n in (_night_before(night), night)
        for f in store.read_index(n)
        if f.mode is Mode.DAY and f.bad is None and f.captured_at.astimezone(tz).date() == day
    ]
    out.sort(key=lambda f: f.captured_at)
    return out


def build_day(
    store: ImageStore,
    night: str,
    tz: tzinfo,
    cfg: DayConfig | None = None,
    products: ProductConfig | None = None,
) -> DayProducts:
    """Build the day timelapse and day keogram of the local date `night`."""
    cfg = cfg or DayConfig()
    pcfg = products or ProductConfig()
    result = DayProducts(night)
    present = [
        p
        for f in day_frames(store, night, tz)
        if (p := store.night_dir(f.night_id) / f.name).is_file()
    ]
    result.frames = len(present)
    folder = store.night_dir(night)
    folder.mkdir(parents=True, exist_ok=True)
    thumbs = folder / "thumbnails"
    if not present:
        result.skipped = {DAY_TIMELAPSE: "no day frames", DAY_KEOGRAM: "no day frames"}
        return result
    if cfg.keogram:
        keo = keogram(present, pcfg)
        if keo is None:
            result.skipped[DAY_KEOGRAM] = "no readable frames"
        else:
            save_jpeg(keo, folder / DAY_KEOGRAM)
            thumbnail(keo, thumbs / DAY_KEOGRAM)
            result.built.append(DAY_KEOGRAM)
    else:
        result.skipped[DAY_KEOGRAM] = "switched off"
    reason = timelapse(present, folder / DAY_TIMELAPSE, pcfg) if cfg.timelapse else "switched off"
    if reason is None:
        thumbnail(present[len(present) // 2], thumbs / "day-timelapse.jpg")
        result.built.append(DAY_TIMELAPSE)
    else:
        result.skipped[DAY_TIMELAPSE] = reason
    durations = (
        {DAY_TIMELAPSE: round(len(present) / pcfg.timelapse_fps, 1)}
        if DAY_TIMELAPSE in result.built
        else {}
    )
    tmp = folder / (DAY_MANIFEST + ".tmp")
    tmp.write_text(json.dumps({"built": result.built, "duration_s": durations}))
    tmp.replace(folder / DAY_MANIFEST)
    return result


def day_products(store: ImageStore, night: str) -> list[dict[str, object]]:
    """The local day products of a night folder for the archive (roadmap L21)."""
    folder = store.night_dir(night)
    try:
        data: object = json.loads((folder / DAY_MANIFEST).read_text())
    except (OSError, ValueError):
        data = {}
    durations: dict[str, object] = {}
    if isinstance(data, dict):
        raw = cast("dict[str, object]", data).get("duration_s")
        if isinstance(raw, dict):
            durations = dict(cast("dict[str, object]", raw))
    out: list[dict[str, object]] = []
    for name, ctype in DAY_NAMES.items():
        path = folder / name
        if path.is_file():
            out.append(
                {
                    "kind": name.removesuffix(".mp4").removesuffix(".jpg").replace("-", "_"),
                    "name": name,
                    "content_type": ctype,
                    "size": path.stat().st_size,
                    "thumbnail": (folder / "thumbnails" / (Path(name).stem + ".jpg")).is_file(),
                    "duration_s": durations.get(name),
                }
            )
    return out


class DuskDetector:
    """Says which date's day just ended when the mode switches from day to night."""

    def __init__(self) -> None:
        self._prev: FrameInfo | None = None

    def feed(self, info: FrameInfo) -> str | None:
        prev, self._prev = self._prev, info
        if prev is not None and prev.mode is Mode.DAY and info.mode is Mode.NIGHT:
            return info.night_id
        return None


def build_mini(store: ImageStore, out: Path, now: datetime, cfg: ProductConfig) -> str | None:
    """The last hour's good frames as a small video (SPEC §5.2); None on success."""
    since = now - MINI_WINDOW
    frames = [
        store.night_dir(f.night_id) / f.name
        for n in store.nights()[:2]
        for f in store.read_index(n)
        if f.captured_at >= since and f.bad is None
    ]
    frames = sorted((p for p in frames if p.is_file()), key=lambda p: p.name)
    out.parent.mkdir(parents=True, exist_ok=True)
    return timelapse(
        frames, out, replace(cfg, timelapse_max_width=MINI_MAX_WIDTH, timelapse_fps=25)
    )


class MiniTimelapse:
    """Renders `<data>/live/mini.mp4` every 10 minutes in its own thread."""

    def __init__(
        self, store: ImageStore, out: Path, cfg: ProductConfig, every_s: float = MINI_EVERY_S
    ) -> None:
        self._store = store
        self._out = out
        self._cfg = cfg
        self._every = every_s
        self._stop = threading.Event()

    def start(self) -> None:
        def run() -> None:
            while not self._stop.wait(self._every):
                try:
                    reason = build_mini(self._store, self._out, datetime.now(UTC), self._cfg)
                    if reason is not None:
                        log.debug("mini timelapse skipped: %s", reason)
                except Exception:  # never stop for one failure
                    log.exception("mini timelapse failed")

        threading.Thread(target=run, name="mini-timelapse", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
