"""What the importers of other programs share (roadmap L18): the report, placing files by
hard link or copy, thumbnails and the video length."""

from __future__ import annotations

import logging
import os
import shutil
import stat
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image as PILImage

from allskyhub_agent.importer.privileges import CAP_FOWNER, capability
from allskyhub_agent.store.images import THUMB_WIDTH

log = logging.getLogger(__name__)

ROOT_UID = 0


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


def _ffprobe(path: Path, args: list[str]) -> str | None:
    """ffprobe's output for `path`. ffprobe gets the file opened here (/dev/fd/N): the
    import runs as the agent and only this process may read the other program's folders
    (see privileges.py)."""
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        return None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    except OSError:
        return None
    try:
        cmd = [ffprobe, "-v", "error", *args, "-of", "csv=p=0", f"/dev/fd/{fd}"]
        out = subprocess.run(  # noqa: S603
            cmd, capture_output=True, text=True, timeout=30, check=False, pass_fds=(fd,)
        )
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        os.close(fd)


def video_duration(path: Path) -> float | None:
    out = _ffprobe(path, ["-show_entries", "format=duration"])
    try:
        return round(float(out), 1) if out else None
    except ValueError:
        return None


def video_codec(path: Path) -> str | None:
    """The first video stream's codec ("h264", "hevc", ...); None without ffprobe."""
    return _ffprobe(path, ["-select_streams", "v:0", "-show_entries", "stream=codec_name"]) or None


class FilePlacer:
    """Hard links (or copies) another program's files into the agent's storage."""

    def __init__(
        self, copy: bool, owner: tuple[int, int] | None, sources: Iterable[Path] = ()
    ) -> None:
        self._copy = copy
        self._owner = owner
        # Security review 2026-11-04: the folders of the other program everything taken
        # over must lie in, resolved once, and the owners whose files are never taken over
        # (cli.py: root and the agent, when the import was started as root).
        self._sources = [p.resolve() for p in sources]
        self.refused_owners: frozenset[int] = frozenset()

    def _inside(self, path: Path) -> bool:
        return any(path.is_relative_to(root) for root in self._sources)

    def _source(self, path: Path) -> Path | None:
        """`path` as a regular file inside the other program's folders and not owned by a
        refused owner, links resolved; None otherwise. A link there to a file elsewhere
        (/etc/shadow, the agent's key) must never be taken over (security review
        2026-11-04)."""
        try:
            real = path.resolve(strict=True)
            st = real.stat()
        except (OSError, RuntimeError):
            return None
        if not stat.S_ISREG(st.st_mode) or not self._inside(real):
            return None
        if st.st_uid in self.refused_owners:
            return None
        return real

    def _open_source(self, path: Path) -> int | None:
        """Like _source, but an open file: what was checked is what gets linked or copied,
        even if a folder is swapped for a link in between."""
        real = self._source(path)
        if real is None:
            return None
        try:
            fd = os.open(real, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except OSError:
            return None
        try:
            st = os.fstat(fd)
            opened = Path(f"/proc/self/fd/{fd}").readlink()
        except OSError:
            os.close(fd)
            return None
        if (not stat.S_ISREG(st.st_mode) or st.st_uid in self.refused_owners
                or not self._inside(opened)):  # fmt: skip
            os.close(fd)
            return None
        return fd

    def _chown(self, path: Path) -> None:
        """Run as root (hard links need it): what this import creates belongs to the agent."""
        if self._owner is not None and os.geteuid() == 0:
            os.chown(path, *self._owner, follow_symlinks=False)

    def hand_over(self, top: Path) -> None:
        """Run as root: what the import made under `top` that still belongs to root (events,
        thumbnails, indexes) goes to the agent, or its cleanup cannot delete those nights
        (SPEC §4.5). Hard links of the other program's files keep their owner."""
        if self._owner is None or os.geteuid() != 0 or not top.is_dir():
            return
        for path in [top, *top.rglob("*")]:
            try:
                if path.lstat().st_uid == ROOT_UID:
                    os.chown(path, *self._owner, follow_symlinks=False)
            except OSError as exc:
                log.warning("cannot hand %s over to the agent: %s", path, exc)

    def _mkdir(self, path: Path) -> None:
        missing = [p for p in [path, *path.parents] if not p.exists()]
        path.mkdir(parents=True, exist_ok=True)
        for p in missing:
            self._chown(p)

    def _place(self, src: Path, dest: Path, report: ImportReport, dry: bool) -> bool | None:
        """Hard link (or copy) `src` to `dest`; False if `dest` already exists, None if
        `src` is no file of the other program (see _source)."""
        if dest.exists():
            return False
        fd = self._open_source(src)
        if fd is None:
            log.warning("%s: not a file of the imported program, skipped", src)
            report.skip("not a file in the imported folders")
            return None
        try:
            st = os.fstat(fd)
            report.bytes += st.st_size
            if dry:
                return True
            self._mkdir(dest.parent)
            if not self._copy:
                try:
                    # The opened file itself, not the name again. CAP_FOWNER only for this:
                    # a hard link to another user's file (fs.protected_hardlinks).
                    # (A dir fd, so that Python uses linkat and follows the /proc link.)
                    parent = os.open(dest.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
                    try:
                        with capability(CAP_FOWNER):
                            os.link(f"/proc/self/fd/{fd}", dest.name, dst_dir_fd=parent)
                    finally:
                        os.close(parent)
                    report.linked += 1
                    return True
                except OSError:
                    pass  # another disk, or not allowed: copy instead
            tmp = dest.with_name(dest.name + ".tmp")
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC
            out = os.open(tmp, flags, 0o600)
            with (
                os.fdopen(out, "wb") as fdst,
                os.fdopen(fd, "rb", closefd=False) as fsrc,
            ):
                shutil.copyfileobj(fsrc, fdst)
                os.fchmod(fdst.fileno(), stat.S_IMODE(st.st_mode))
                os.utime(fdst.fileno(), ns=(st.st_atime_ns, st.st_mtime_ns))
            tmp.replace(dest)
            self._chown(dest)
            report.copied += 1
            return True
        finally:
            os.close(fd)
