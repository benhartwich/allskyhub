"""Frame storage (SPEC §4.5): JPEG plus thumbnail per night folder, retention."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from pathlib import Path

from PIL import Image as PILImage
from pydantic import ValidationError

from allskyhub_agent.adapters.camera import Image
from allskyhub_protocol import FrameInfo

THUMB_WIDTH = 400


def _disk_usage(path: Path) -> tuple[int, int]:
    """(total, free) bytes of the file system that holds `path`."""
    u = shutil.disk_usage(path)
    return u.total, u.free


INDEX_NAME = "frames.jsonl"


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
            shutil.rmtree(d)
            removed.append(d.name)
        return removed

    def cleanup(self, now: datetime, keep_days: int) -> list[str]:
        """Delete night folders older than `keep_days`; returns the removed night ids."""
        oldest = night_id(now, self._tz)
        cutoff = (datetime.strptime(oldest, "%Y%m%d") - timedelta(days=keep_days)).strftime(  # noqa: DTZ007
            "%Y%m%d"
        )
        removed: list[str] = []
        if not self._images.is_dir():
            return removed
        for d in sorted(self._images.iterdir()):
            if d.is_dir() and len(d.name) == 8 and d.name.isdigit() and d.name < cutoff:
                shutil.rmtree(d)
                removed.append(d.name)
        return removed
