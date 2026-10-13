"""Keolapse: the keograms of many nights side by side (SPEC §5.4, roadmap L21).

Built on request from the existing keograms and cached until a night in the range changes.
"""

from __future__ import annotations

import hashlib
import io
import re
from pathlib import Path

from PIL import Image as PILImage

from allskyhub_agent.products.build import KEOGRAM
from allskyhub_agent.store.images import ImageStore

HEIGHT = 540
COLUMN = 48
MAX_NIGHTS = 92
GAP = (40, 40, 40)
_NIGHT = re.compile(r"^\d{8}$")


def nights_between(store: ImageStore, first: str, last: str) -> list[str]:
    """The stored nights from `first` to `last`, oldest first, at most the newest 92."""
    if not (_NIGHT.match(first) and _NIGHT.match(last)):
        return []
    if first > last:
        first, last = last, first
    picked = sorted(n for n in store.nights() if first <= n <= last)
    return picked[-MAX_NIGHTS:]


def _key(store: ImageStore, nights: list[str]) -> str:
    h = hashlib.sha256()
    for n in nights:
        p = store.night_dir(n) / KEOGRAM
        try:
            st = p.stat()
            h.update(f"{n}:{st.st_size}:{st.st_mtime_ns};".encode())
        except OSError:
            h.update(f"{n}:-;".encode())
    return h.hexdigest()[:16]


def keolapse(store: ImageStore, first: str, last: str, cache: Path) -> bytes | None:
    """The JPEG for the range, from the cache when nothing changed; None without nights."""
    nights = nights_between(store, first, last)
    if not nights:
        return None
    cached = cache / f"keolapse-{nights[0]}-{nights[-1]}-{_key(store, nights)}.jpg"
    try:
        return cached.read_bytes()
    except OSError:
        pass
    canvas = PILImage.new("RGB", (COLUMN * len(nights), HEIGHT), GAP)
    for i, n in enumerate(nights):
        try:
            with PILImage.open(store.night_dir(n) / KEOGRAM) as im:
                strip = im.convert("RGB").resize((COLUMN, HEIGHT))
        except OSError:
            continue  # no keogram: a grey gap
        canvas.paste(strip, (i * COLUMN, 0))
    buf = io.BytesIO()
    canvas.save(buf, format="JPEG", quality=85)
    data = buf.getvalue()
    try:
        cache.mkdir(parents=True, exist_ok=True)
        for old in cache.glob(f"keolapse-{nights[0]}-{nights[-1]}-*.jpg"):
            old.unlink(missing_ok=True)
        tmp = cached.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(cached)
    except OSError:
        pass  # a read-only cache only costs time
    return data
