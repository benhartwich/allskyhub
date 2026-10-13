"""What the importers of other programs share (roadmap L18): the report, placing files by
hard link or copy, thumbnails and the video length."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image as PILImage

from allskyhub_agent.store.images import THUMB_WIDTH


def system_timezone() -> tzinfo:
    """Allsky and indi-allsky keep their times in the system's local time."""
    try:
        return ZoneInfo(Path("/etc/timezone").read_text().strip())
    except (OSError, ValueError, KeyError):
        return UTC


@dataclass
class ImportReport:
    folders: int = 0
    images: int = 0
    images_skipped: int = 0
    products: int = 0
    meteors: int = 0
    lightning: int = 0
    episodes: int = 0  # aurora and NLC
    sky: int = 0
    linked: int = 0
    copied: int = 0
    bytes: int = 0
    nights: set[str] = field(default_factory=set[str])
    # Files that cannot come over (not JPEG, a WebM timelapse, ...), by reason.
    unsupported: dict[str, int] = field(default_factory=dict[str, int])

    def skip(self, reason: str) -> None:
        self.unsupported[reason] = self.unsupported.get(reason, 0) + 1


def thumbnail(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with PILImage.open(src) as im:
        im.draft("RGB", (THUMB_WIDTH, THUMB_WIDTH))  # JPEG: decode at a smaller scale, fast
        rgb = im.convert("RGB")
    h = max(1, round(rgb.height * THUMB_WIDTH / rgb.width))
    tmp = dest.with_name(dest.stem + ".tmp.jpg")
    rgb.resize((THUMB_WIDTH, h)).save(tmp, format="JPEG", quality=80)
    tmp.replace(dest)


def video_duration(path: Path) -> float | None:
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        return None
    cmd = [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)  # noqa: S603
        return round(float(out.stdout.strip()), 1)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def video_codec(path: Path) -> str | None:
    """The first video stream's codec ("h264", "hevc", ...); None without ffprobe."""
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        return None
    cmd = [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
           "stream=codec_name", "-of", "csv=p=0", str(path)]  # fmt: skip
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)  # noqa: S603
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


class FilePlacer:
    """Hard links (or copies) another program's files into the agent's storage."""

    def __init__(self, copy: bool, owner: tuple[int, int] | None) -> None:
        self._copy = copy
        self._owner = owner

    def _chown(self, path: Path) -> None:
        """Run as root (hard links need it): what this import creates belongs to the agent."""
        if self._owner is not None and os.geteuid() == 0:
            os.chown(path, *self._owner, follow_symlinks=False)

    def _mkdir(self, path: Path) -> None:
        missing = [p for p in [path, *path.parents] if not p.exists()]
        path.mkdir(parents=True, exist_ok=True)
        for p in missing:
            self._chown(p)

    def _place(self, src: Path, dest: Path, report: ImportReport, dry: bool) -> bool:
        """Hard link (or copy) `src` to `dest`; False if `dest` already exists."""
        if dest.exists():
            return False
        report.bytes += src.stat().st_size
        if dry:
            return True
        self._mkdir(dest.parent)
        if not self._copy:
            try:
                os.link(src, dest)
                report.linked += 1
                return True
            except OSError:
                pass  # another disk, or not allowed: copy instead
        tmp = dest.with_name(dest.name + ".tmp")
        shutil.copy2(src, tmp)
        tmp.replace(dest)
        self._chown(dest)
        report.copied += 1
        return True
