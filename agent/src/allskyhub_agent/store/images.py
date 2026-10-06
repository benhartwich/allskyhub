"""Frame storage (SPEC §4.5): JPEG plus thumbnail per night folder, retention."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from pathlib import Path

from PIL import Image as PILImage

from allskyhub_agent.adapters.camera import Image

THUMB_WIDTH = 400


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

    def path_for(self, night: str, name: str, thumbnail: bool = False) -> Path | None:
        """Stored image (or its thumbnail) by night id and name; None if it does not exist.

        Only plain names inside the night folder are accepted, never paths.
        """
        if not (len(night) == 8 and night.isdigit()) or "/" in name or name.startswith("."):
            return None
        folder = self._images / night
        path = (folder / "thumbnails" / name) if thumbnail else (folder / name)
        return path if path.is_file() else None

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
