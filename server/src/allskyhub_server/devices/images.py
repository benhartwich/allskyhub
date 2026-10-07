"""Images per device on the file system (SPEC §6.5).

Every image the hub asked for is archived per night:
``<root>/<device>/<night_id>/<variant>/<name>``. ``latest-<variant>.jpg`` is a hard link to
the newest one, so the latest image costs no extra space. Retention: maintenance.py.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from allskyhub_protocol import FrameVariant

JPEG_MAGIC = b"\xff\xd8\xff"


class ImageStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, device_id: str, variant: FrameVariant) -> Path:
        """The newest image of a device."""
        return self.root / device_id / f"latest-{variant.value}.jpg"

    def frame_path(self, device_id: str, night_id: str, name: str, variant: FrameVariant) -> Path:
        """``night_id`` and ``name`` are validated by the API (no path separators)."""
        return self.root / device_id / night_id / variant.value / name

    def save_frame(
        self, device_id: str, night_id: str, name: str, variant: FrameVariant, data: bytes
    ) -> None:
        """Archive the image and make it the latest one; atomic for readers of both."""
        target = self.frame_path(device_id, night_id, name, variant)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".part")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            Path(tmp).replace(target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        latest = self.path(device_id, variant)
        link = latest.with_suffix(".link")
        link.unlink(missing_ok=True)
        os.link(target, link)
        link.replace(latest)

    def delete_frame(self, device_id: str, night_id: str, name: str, variant: FrameVariant) -> None:
        path = self.frame_path(device_id, night_id, name, variant)
        path.unlink(missing_ok=True)
        # Drop empty variant and night directories.
        for directory in (path.parent, path.parent.parent):
            try:
                directory.rmdir()
            except OSError:
                break

    def delete_device(self, device_id: str) -> None:
        """Everything of a device: latest images and the whole archive."""
        shutil.rmtree(self.root / device_id, ignore_errors=True)
