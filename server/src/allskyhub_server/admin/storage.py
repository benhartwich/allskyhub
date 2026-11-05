"""The images' own disk (netcup local block storage, Benjamin 2026-11-05): how full it is,
whether the disk under it got bigger than its file system, and growing the file system
from the admin page.

The hub cannot grow a file system itself. Like a deploy (admin/deploy.py) it leaves
``<data_dir>/storage/request`` and the root helper ``deploy/storage/allskyhub-storage.sh``
(started by ``allskyhub-storage.path``) runs ``resize2fs`` on the disk mounted at
``/srv/allskyhub-img``, never on anything named in the request, and answers in ``state``
and ``log``. ext4 grows while mounted; nothing stops.
"""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass
from pathlib import Path

STATES = ("requested", "running", "ok", "failed")
LOG_TAIL = 40
# The disk must be this much bigger than its file system before growing is offered:
# ext4 counts its own tables and journal (about 2 %) out of the size statvfs reports.
GROW_MIN_BYTES = 1_000_000_000
GROW_MIN_SHARE = 0.04


class StorageError(Exception):
    pass


@dataclass(frozen=True)
class Disk:
    device: str  # e.g. "vdb"
    fs_bytes: int
    used_bytes: int
    disk_bytes: int | None  # the block device; None when it cannot be read

    @property
    def used_pct(self) -> float:
        return 100 * self.used_bytes / self.fs_bytes if self.fs_bytes else 0.0

    @property
    def growable(self) -> bool:
        if self.disk_bytes is None:
            return False
        margin = max(GROW_MIN_BYTES, GROW_MIN_SHARE * self.disk_bytes)
        return self.disk_bytes - self.fs_bytes > margin


@dataclass(frozen=True)
class Job:
    state: str  # idle, requested, running, ok, failed
    log: str
    changed_at: dt.datetime | None

    @property
    def busy(self) -> bool:
        return self.state in ("requested", "running")


def _source(path: Path, mountinfo: str) -> str | None:
    """The block device mounted at ``path`` (also through a bind mount), from mountinfo."""
    best: tuple[int, str] | None = None
    for line in mountinfo.splitlines():
        fields = line.split()
        if " - " not in line or len(fields) < 5:
            continue
        target = fields[4]
        source = line.split(" - ", 1)[1].split()[1]
        if (str(path) == target or str(path).startswith(target.rstrip("/") + "/")) and (
            best is None or len(target) > best[0]
        ):
            best = (len(target), source)
    return best[1] if best else None


def disk(
    images: Path,
    mountinfo: Path = Path("/proc/self/mountinfo"),
    sys_block: Path = Path("/sys/class/block"),
) -> Disk | None:
    """The disk under the images, or None when they are on no device of their own."""
    try:
        source = _source(images, mountinfo.read_text())
        st = os.statvfs(images)
    except OSError:
        return None
    if not source or not source.startswith("/dev/"):
        return None
    name = source.removeprefix("/dev/")
    fs_bytes = st.f_blocks * st.f_frsize
    used = (st.f_blocks - st.f_bfree) * st.f_frsize
    try:
        sectors = int((sys_block / name / "size").read_text().strip())
        size: int | None = sectors * 512  # the kernel counts 512-byte sectors here
    except (OSError, ValueError):
        size = None
    return Disk(device=name, fs_bytes=fs_bytes, used_bytes=used, disk_bytes=size)


def spool(data_dir: Path) -> Path:
    return data_dir / "storage"


def job(data_dir: Path) -> Job:
    """What the root helper last reported (or that a request is still waiting)."""
    folder = spool(data_dir)

    def read(name: str) -> str:
        try:
            return (folder / name).read_text(errors="replace")
        except OSError:
            return ""

    changed: dt.datetime | None = None
    for name in ("state", "request"):
        try:
            mtime = dt.datetime.fromtimestamp((folder / name).stat().st_mtime, dt.UTC)
        except OSError:
            continue
        changed = mtime if changed is None else max(changed, mtime)
    state = "requested" if (folder / "request").is_file() else read("state").strip()
    return Job(
        state=state if state in STATES else "idle",
        log="\n".join(read("log").splitlines()[-LOG_TAIL:]),
        changed_at=changed,
    )


def request(data_dir: Path, found: Disk | None, by: str) -> None:
    """Leave the request; refuses what the helper would have nothing to do for."""
    if found is None or not found.growable:
        raise StorageError("Die Platte ist nicht größer als ihr Dateisystem: nichts zu tun.")
    if job(data_dir).busy:
        raise StorageError("Das Vergrößern läuft schon.")
    folder = spool(data_dir)
    folder.mkdir(mode=0o750, exist_ok=True)
    tmp = folder / "request.tmp"
    tmp.write_text(f"{by}\n")
    tmp.replace(folder / "request")
