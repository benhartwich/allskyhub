"""The overlay layout of the local UI (SPEC §7 Overlay, roadmap L11).

What is drawn over the image, the same for every browser: for each corner a list of
fields, up to two own text lines, a size, the compass and the constellations. Kept in
`<data>/overlay.json`. It is only ever drawn over the image, never into stored frames.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, cast

FIELDS = (
    "time", "date", "exposure", "gain", "sensor_temp", "sun", "moon", "cloud_cover", "sqm",
    "stars", "mode", "text1", "text2",
    "env_temp", "humidity", "dewpoint", "heater",  # SPEC §7.11: from the weather sensor
)  # fmt: skip
CORNERS = ("tl", "tr", "bl", "br")
SIZES = ("small", "normal", "large")
MAX_TEXT = 60

DEFAULT: dict[str, Any] = {
    "enabled": True,
    "corners": {
        "tl": ["date", "time"],
        "tr": ["exposure", "gain", "sensor_temp"],
        "bl": ["sun", "moon"],
        "br": ["cloud_cover", "sqm"],
    },
    "texts": ["", ""],
    "size": "normal",
    "compass": True,
    "stars": True,
}


def parse(raw: object) -> dict[str, Any] | None:
    """A valid layout from a request body, or None."""
    if not isinstance(raw, dict):
        return None
    d = cast("dict[str, Any]", raw)
    corners_raw: object = d.get("corners")
    if not isinstance(corners_raw, dict):
        return None
    corners: dict[str, list[str]] = {}
    seen: set[str] = set()
    for c in CORNERS:
        items: object = cast("dict[str, Any]", corners_raw).get(c, [])
        if not isinstance(items, list):
            return None
        names: list[str] = []
        for item in cast("list[object]", items):
            if not isinstance(item, str) or item not in FIELDS or item in seen:
                return None
            seen.add(item)
            names.append(item)
        corners[c] = names
    texts_raw: object = d.get("texts", ["", ""])
    if not isinstance(texts_raw, list) or len(cast("list[object]", texts_raw)) > 2:
        return None
    texts: list[str] = []
    for t in cast("list[object]", texts_raw):
        if not isinstance(t, str) or len(t) > MAX_TEXT:
            return None
        texts.append(t.strip())
    texts += [""] * (2 - len(texts))
    size = d.get("size", "normal")
    if size not in SIZES:
        return None
    flags = {k: d.get(k, DEFAULT[k]) for k in ("enabled", "compass", "stars")}
    if not all(isinstance(v, bool) for v in flags.values()):
        return None
    return {"corners": corners, "texts": texts, "size": size, **flags}


class OverlayLayout:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    def get(self) -> dict[str, Any]:
        try:
            raw: object = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return json.loads(json.dumps(DEFAULT))
        return parse(raw) or json.loads(json.dumps(DEFAULT))

    def set(self, raw: object) -> dict[str, Any] | None:
        layout = parse(raw)
        if layout is None:
            return None
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(layout), encoding="utf-8")
            tmp.replace(self._path)
        return layout
