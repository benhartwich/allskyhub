"""Latest images per device on the file system (SPEC §6.5).

Only the newest full image and thumbnail of each device are kept for now; per-night
archives come with the products (M4).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from allskyhub_protocol import FrameVariant

JPEG_MAGIC = b"\xff\xd8\xff"


class ImageStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, device_id: str, variant: FrameVariant) -> Path:
        return self.root / device_id / f"latest-{variant.value}.jpg"

    def save(self, device_id: str, variant: FrameVariant, data: bytes) -> None:
        """Atomic replace, so readers never see a half-written image."""
        target = self.path(device_id, variant)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".part")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            Path(tmp).replace(target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def delete_device(self, device_id: str) -> None:
        for variant in FrameVariant:
            self.path(device_id, variant).unlink(missing_ok=True)
