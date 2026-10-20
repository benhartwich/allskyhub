"""Frame storage (SPEC §4.5): JPEG plus thumbnail per night folder, retention."""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from pathlib import Path

from PIL import Image as PILImage
from pydantic import ValidationError

from allskyhub_agent.adapters.camera import Image
from allskyhub_protocol import FrameInfo, SkyMetrics

log = logging.getLogger(__name__)

THUMB_WIDTH = 400


def _disk_usage(path: Path) -> tuple[int, int]:
    """(total, free) bytes of the file system that holds `path`."""
    u = shutil.disk_usage(path)
    return u.total, u.free


INDEX_NAME = "frames.jsonl"
SKY_NAME = "sky.jsonl"


def night_id(captured_at: datetime, local_tz: tzinfo) -> str:
    """Date of the evening a night started: frames before local noon count to the day before."""
    local = captured_at.astimezone(local_tz)
    day: date = local.date() if local.hour >= 12 else local.date() - timedelta(days=1)
    return day.strftime("%Y%m%d")


@dataclass(frozen=True)
class StoredFrame:
    night_id: str
    name: str
    path: Path
    thumbnail: Path


class ImageStore:
    def __init__(self, root: Path, local_tz: tzinfo, quality: int = 90) -> None:
        self._images = root / "images"
        self._tz = local_tz
        self._quality = quality

    @property
    def images_dir(self) -> Path:
        return self._images

    @property
    def local_tz(self) -> tzinfo:
        return self._tz

    def save(self, image: Image, captured_at: datetime) -> StoredFrame:
        nid = night_id(captured_at, self._tz)
        stamp = captured_at.astimezone(self._tz).strftime("%Y%m%d%H%M%S")
        name = f"image-{stamp}.jpg"
        folder = self._images / nid
        thumbs = folder / "thumbnails"
        thumbs.mkdir(parents=True, exist_ok=True)

        pil = PILImage.fromarray(image)
        path = folder / name
        tmp = path.with_suffix(".tmp")
        pil.save(tmp, format="JPEG", quality=self._quality)
        tmp.replace(path)  # never leave a half-written image under its final name

        h = max(1, round(pil.height * THUMB_WIDTH / pil.width))
        thumb = thumbs / name
        pil.resize((THUMB_WIDTH, h)).save(thumb, format="JPEG", quality=80)
        return StoredFrame(nid, name, path, thumb)

    def night_dir(self, night: str) -> Path:
        return self._images / night

    def append_index(self, info: FrameInfo) -> None:
        """SPEC §5.1: one line per stored frame."""
        folder = self._images / info.night_id
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / INDEX_NAME).open("a", encoding="utf-8") as f:
            f.write(info.model_dump_json() + "\n")

    def read_index(self, night: str) -> list[FrameInfo]:
        """Frames of a night in capture order; damaged lines are skipped (SPEC §5.1)."""
        path = self._images / night / INDEX_NAME
        out: list[FrameInfo] = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return out
        for line in lines:
            try:
                out.append(FrameInfo.model_validate_json(line))
            except ValidationError:
                continue
        out.sort(key=lambda f: f.captured_at)
        return out

    def append_sky(self, m: SkyMetrics) -> None:
        """SPEC §5.1: the sky measured on a frame, for the local charts (roadmap L6)."""
        folder = self._images / m.night_id
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / SKY_NAME).open("a", encoding="utf-8") as f:
            f.write(m.model_dump_json() + "\n")

    def read_sky(self, night: str) -> list[SkyMetrics]:
        """Sky samples of a night in time order; damaged lines are skipped."""
        if not (len(night) == 8 and night.isdigit()):
            return []
        try:
            lines = (self._images / night / SKY_NAME).read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out: list[SkyMetrics] = []
        for line in lines:
            try:
                out.append(SkyMetrics.model_validate_json(line))
            except ValidationError:
                continue
        out.sort(key=lambda m: m.at)
        return out

    def nights(self) -> list[str]:
        """Night ids with a folder, newest first."""
        if not self._images.is_dir():
            return []
        return sorted(
            (
                p.name
                for p in self._images.iterdir()
                if p.is_dir() and len(p.name) == 8 and p.name.isdigit()
            ),
            reverse=True,
        )

    def remove(self, night: str, name: str) -> None:
        """Delete one stored frame and its thumbnail (day capture `live`, SPEC §4.1)."""
        for thumb in (False, True):
            path = self.path_for(night, name, thumb)
            if path is not None:
                path.unlink(missing_ok=True)

    def path_for(self, night: str, name: str, thumbnail: bool = False) -> Path | None:
        """Stored image (or its thumbnail) by night id and name; None if it does not exist.

        Only plain names inside the night folder are accepted, never paths.
        """
        if not (len(night) == 8 and night.isdigit()) or "/" in name or name.startswith("."):
            return None
        folder = self._images / night
        path = (folder / "thumbnails" / name) if thumbnail else (folder / name)
        return path if path.is_file() else None

    def ensure_free(
        self,
        now: datetime,
        min_free_pct: float = 10.0,
        usage: Callable[[Path], tuple[int, int]] | None = None,
    ) -> list[str]:
        """Delete the oldest nights until at least `min_free_pct` of the disk is free
        (SPEC §4.5). The current night is never deleted. Returns the removed night ids."""
        measure = usage or _disk_usage
        current = night_id(now, self._tz)
        removed: list[str] = []
        if not self._images.is_dir():
            return removed
        nights = sorted(
            d
            for d in self._images.iterdir()
            if d.is_dir() and len(d.name) == 8 and d.name.isdigit()
        )
        for d in nights:
            total, free = measure(self._images)
            if total <= 0 or 100.0 * free / total >= min_free_pct or d.name >= current:
                break
            if _remove_night(d):
                removed.append(d.name)
        return removed

    def cleanup(
        self, now: datetime, keep_days: int, keep_product_days: int | None = None
    ) -> list[str]:
        """Retention by days (SPEC §4.5). Nights older than `keep_product_days` are deleted;
        older than `keep_days` they lose their frames but keep the night and day products,
        detections and sky data. Returns the night ids deleted as a whole."""
        today = datetime.strptime(night_id(now, self._tz), "%Y%m%d")  # noqa: DTZ007
        keep_all = max(keep_days, keep_product_days or keep_days)
        frames_cutoff = (today - timedelta(days=keep_days)).strftime("%Y%m%d")
        cutoff = (today - timedelta(days=keep_all)).strftime("%Y%m%d")
        removed: list[str] = []
        if not self._images.is_dir():
            return removed
        for d in sorted(self._images.iterdir()):
            if not (d.is_dir() and len(d.name) == 8 and d.name.isdigit()):
                continue
            if d.name < cutoff:
                if _remove_night(d):
                    removed.append(d.name)
            elif d.name < frames_cutoff:
                try:
                    self._drop_frames(d.name)
                except OSError as exc:
                    log.warning("cannot remove the frames of night %s: %s", d.name, exc)
        return removed

    def _drop_frames(self, night: str) -> None:
        """The frames of a night, their thumbnails and their index; nothing else."""
        index = self._images / night / INDEX_NAME
        if not index.is_file():
            return  # done before
        for f in self.read_index(night):
            self.remove(night, f.name)
        index.unlink(missing_ok=True)


def _remove_night(d: Path) -> bool:
    """One night that cannot be deleted (a file the agent may not remove) is logged and
    skipped; it must not stop the cleanup of the others."""
    try:
        shutil.rmtree(d)
    except OSError as exc:
        log.warning("cannot remove night %s: %s", d.name, exc)
        return False
    return True
