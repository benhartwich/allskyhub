"""Building the night products (SPEC §5.2).

Images are read one at a time, so memory stays at a few frames' worth even for long nights.
Every output is written to a temp name and renamed, so a power cut never leaves a broken
product under its final name.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import numpy as np
import numpy.typing as npt
from PIL import Image as PILImage

from allskyhub_agent.store.images import THUMB_WIDTH, ImageStore
from allskyhub_protocol import PRODUCT_NAMES, FrameInfo, Mode, ProductFile, ProductKind, Products

log = logging.getLogger(__name__)

MANIFEST = "products.json"
KEOGRAM = "keogram.jpg"
STARTRAILS = "startrails.jpg"
TIMELAPSE = "timelapse.mp4"

U8 = npt.NDArray[np.uint8]


@dataclass(frozen=True)
class ProductConfig:
    startrails_max_mean: float = 0.35
    startrails_min_frames: int = 10
    # SPEC §5.2 (roadmap L32): a frame lit up for one exposure by a light near the camera
    # (a lamp, a car) is left out of the startrails. It must be brighter than both
    # neighbours by this share after evening out exposure and gain, and by this much in
    # mean (0..1) as stored.
    flash_rel: float = 0.15
    flash_abs: float = 6 / 255
    keogram_band: int = 3
    keogram_max_height: int = 1080
    timelapse_fps: int = 25
    timelapse_max_width: int = 1920
    timelapse_crf: int = 23  # x264: lower is better and larger
    # Which products to build (roadmap L9).
    keogram: bool = True
    startrails: bool = True
    timelapse: bool = True
    ffmpeg: str = "ffmpeg"


@dataclass
class NightProducts:
    night_id: str
    frames: int = 0
    built: list[str] = field(default_factory=list[str])
    skipped: dict[str, str] = field(default_factory=dict[str, str])


def _load(path: Path) -> U8 | None:
    try:
        with PILImage.open(path) as im:
            return np.asarray(im.convert("RGB"), dtype=np.uint8)
    except OSError:
        log.warning("cannot read %s, skipped", path)
        return None


def save_jpeg(arr: U8, path: Path, quality: int = 90) -> None:
    tmp = path.with_name(path.stem + ".tmp.jpg")
    PILImage.fromarray(arr).save(tmp, format="JPEG", quality=quality)
    tmp.replace(path)


def thumbnail(src: Path | U8, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    im = PILImage.open(src) if isinstance(src, Path) else PILImage.fromarray(src)
    with im:
        h = max(1, round(im.height * THUMB_WIDTH / im.width))
        tmp = dest.with_name(dest.stem + ".tmp.jpg")
        im.convert("RGB").resize((THUMB_WIDTH, h)).save(tmp, format="JPEG", quality=80)
        tmp.replace(dest)


def keogram(paths: Iterable[Path], cfg: ProductConfig) -> U8 | None:
    """One column per frame: the averaged vertical band through the image centre."""
    columns: list[U8] = []
    height: int | None = None
    for p in paths:
        img = _load(p)
        if img is None:
            continue
        h, w = img.shape[0], img.shape[1]
        if height is None:
            height = h
        if h != height:
            continue  # resolution changed mid-night; keep the first one
        x0 = max(0, w // 2 - cfg.keogram_band // 2)
        band = img[:, x0 : x0 + cfg.keogram_band].astype(np.float32).mean(axis=1)
        columns.append(band.astype(np.uint8))
    if not columns or height is None:
        return None
    keo: U8 = np.empty((height, len(columns), 3), dtype=np.uint8)
    for i, col in enumerate(columns):
        keo[:, i, :] = col
    if height > cfg.keogram_max_height:
        im = PILImage.fromarray(keo).resize((len(columns), cfg.keogram_max_height))
        keo = np.asarray(im, dtype=np.uint8)
    return keo


def _light(f: FrameInfo) -> float:
    """The frame's mean evened out for exposure and gain (gain in 0.1 dB, SPEC §4.4)."""
    return f.mean / (max(f.exposure_us, 1) * 10 ** (f.gain / 200.0))


def flash_frames(frames: list[FrameInfo], cfg: ProductConfig) -> set[str]:
    """Names of frames brighter than the frames before and after (SPEC §5.2): a light
    switched on for one exposure. A ramp at dusk or an exposure step is never brighter than
    both neighbours; the first and last frame have only one and are kept."""
    out: set[str] = set()
    for a, b, c in zip(frames, frames[1:], frames[2:], strict=False):
        lit = max(_light(a), _light(c))
        if (
            lit > 0
            and _light(b) >= (1.0 + cfg.flash_rel) * lit
            and b.mean - max(a.mean, c.mean) >= cfg.flash_abs
        ):
            out.add(b.name)
    return out


def startrails(frames: list[tuple[Path, float]], cfg: ProductConfig) -> U8 | None:
    """Per-pixel maximum of the dark enough frames; None with too few of them."""
    acc: U8 | None = None
    used = 0
    for path, mean in frames:
        if mean > cfg.startrails_max_mean:
            continue
        img = _load(path)
        if img is None:
            continue
        if acc is None:
            acc = img.copy()
        elif img.shape == acc.shape:
            np.maximum(acc, img, out=acc)
        else:
            continue
        used += 1
    if acc is None or used < cfg.startrails_min_frames:
        return None
    return acc


def timelapse(paths: list[Path], out: Path, cfg: ProductConfig) -> str | None:
    """Encode the frames with ffmpeg; returns None on success, else the reason."""
    exe = shutil.which(cfg.ffmpeg)
    if exe is None:
        return "ffmpeg not found"
    if len(paths) < 2:
        return "fewer than 2 frames"
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as lst:
        for p in paths:
            # concat demuxer list; single quotes in names are escaped as the docs require
            lst.write("file '" + str(p.resolve()).replace("'", "'\\''") + "'\n")
            lst.write(f"duration {1 / cfg.timelapse_fps:.6f}\n")
        list_path = Path(lst.name)
    tmp = out.with_name(out.stem + ".tmp.mp4")
    scale = f"scale='min({cfg.timelapse_max_width},iw)':-2"
    cmd = [
        exe, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0", "-i", str(list_path),
        "-vf", scale, "-r", str(cfg.timelapse_fps),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(cfg.timelapse_crf),
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", str(tmp),
    ]  # fmt: skip
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=3600)  # noqa: S603
    finally:
        list_path.unlink(missing_ok=True)
    if res.returncode != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        return f"ffmpeg failed: {res.stderr.strip()[-300:]}"
    tmp.replace(out)
    return None


def build_night(store: ImageStore, night: str, cfg: ProductConfig | None = None) -> NightProducts:
    """Build keogram, startrails and timelapse of a night's good night-mode frames (SPEC §5.2)."""
    cfg = cfg or ProductConfig()
    result = NightProducts(night)
    folder = store.night_dir(night)
    frames: list[FrameInfo] = [
        f for f in store.read_index(night) if f.mode is Mode.NIGHT and f.bad is None
    ]  # SPEC §4.9: bad frames are left out
    present = [(folder / f.name, f) for f in frames if (folder / f.name).is_file()]
    result.frames = len(present)
    if not present:
        result.skipped = {KEOGRAM: "no night frames", STARTRAILS: "no night frames",
                          TIMELAPSE: "no night frames"}  # fmt: skip
        return result
    paths = [p for p, _ in present]
    thumbs = folder / "thumbnails"

    keo = keogram(paths, cfg) if cfg.keogram else None
    if not cfg.keogram:
        result.skipped[KEOGRAM] = "switched off"
    elif keo is None:
        result.skipped[KEOGRAM] = "no readable frames"
    else:
        save_jpeg(keo, folder / KEOGRAM)
        thumbnail(keo, thumbs / KEOGRAM)
        result.built.append(KEOGRAM)

    flashes = flash_frames(frames, cfg)
    if flashes:
        log.info("night %s: %d flash frames left out of the startrails", night, len(flashes))
    st = (
        startrails([(p, f.mean) for p, f in present if f.name not in flashes], cfg)
        if cfg.startrails
        else None
    )
    if not cfg.startrails:
        result.skipped[STARTRAILS] = "switched off"
    elif st is None:
        result.skipped[STARTRAILS] = (
            f"fewer than {cfg.startrails_min_frames} frames darker than {cfg.startrails_max_mean}"
        )
    else:
        save_jpeg(st, folder / STARTRAILS)
        thumbnail(st, thumbs / STARTRAILS)
        result.built.append(STARTRAILS)

    reason = timelapse(paths, folder / TIMELAPSE, cfg) if cfg.timelapse else "switched off"
    if reason is not None:
        result.skipped[TIMELAPSE] = reason
    else:
        # The middle frame stands in as the timelapse's thumbnail.
        thumbnail(paths[len(paths) // 2], thumbs / "timelapse.jpg")
        result.built.append(TIMELAPSE)

    for name, why in result.skipped.items():
        log.info("night %s: %s skipped (%s)", night, name, why)
    durations = (
        {TIMELAPSE: round(len(paths) / cfg.timelapse_fps, 1)} if TIMELAPSE in result.built else {}
    )
    tmp = folder / (MANIFEST + ".tmp")
    tmp.write_text(json.dumps({"built": result.built, "duration_s": durations}))
    tmp.replace(folder / MANIFEST)
    return result


def _thumb_name(name: str) -> str:
    return Path(name).stem + ".jpg"


def product_path(store: ImageStore, night: str, name: str, thumbnail: bool) -> Path | None:
    """A night product (or its thumbnail) if it exists (SPEC §6.5, `upload_product`)."""
    if not (len(night) == 8 and night.isdigit()):
        return None
    if name not in {n for n, _ in PRODUCT_NAMES.values()}:
        return None
    folder = store.night_dir(night)
    path = folder / "thumbnails" / _thumb_name(name) if thumbnail else folder / name
    return path if path.is_file() else None


def night_products(store: ImageStore, night: str) -> Products | None:
    """The `products` message for a night (SPEC §6.3), or None if it has none."""
    folder = store.night_dir(night)
    durations: dict[str, float] = {}
    # The night's manifest, and the day products' (SPEC §5.2, products/day.py).
    for manifest in (MANIFEST, "day-products.json"):
        try:
            data: object = json.loads((folder / manifest).read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        raw = cast("dict[str, object]", data).get("duration_s")
        if isinstance(raw, dict):
            for k, v in cast("dict[str, object]", raw).items():
                if isinstance(v, int | float):
                    durations[k] = float(v)
    files: list[ProductFile] = []
    for kind in ProductKind:
        name, ctype = PRODUCT_NAMES[kind]
        path = folder / name
        if not path.is_file():
            continue
        files.append(
            ProductFile(
                kind=kind,
                name=name,
                content_type=ctype,  # pyright: ignore[reportArgumentType]
                size=path.stat().st_size,
                thumbnail=(folder / "thumbnails" / _thumb_name(name)).is_file(),
                duration_s=durations.get(name),
            )
        )
    return Products(night_id=night, products=files) if files else None


def newest_products(store: ImageStore) -> Products | None:
    """The newest night that has products (sent after every reconnect, SPEC §6.7)."""
    root = store.images_dir
    if not root.is_dir():
        return None
    for d in sorted((p for p in root.iterdir() if p.is_dir() and p.name.isdigit()), reverse=True):
        products = night_products(store, d.name)
        if products is not None:
            return products
    return None
