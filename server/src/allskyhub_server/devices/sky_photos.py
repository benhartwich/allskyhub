"""Photos of sky objects for the app (allskyhub_sky ``photos.json``), fetched once from
Wikimedia Commons by the hub and kept on disk, so the app talks only to the hub and
Commons learns nothing about its users. Only freely licensed files are listed there
(tools/sky_photos.py); the app shows each one's author and licence.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx

from allskyhub_sky.describe import photos

log = logging.getLogger(__name__)

TIMEOUT_S = 20.0
MAX_BYTES = 4_000_000


def _media_type(body: bytes) -> str | None:
    if body.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if body.startswith(b"\x89PNG"):
        return "image/png"
    return None


class SkyPhotos:
    def __init__(self, root: Path, client: httpx.AsyncClient) -> None:
        self._root = root
        self._client = client
        self._list = photos()
        self._lock = asyncio.Lock()

    def credit(self, key: str) -> dict[str, str] | None:
        return self._list.get(key)

    async def get(self, key: str) -> tuple[bytes, str] | None:
        """The photo and its media type, or None without one."""
        entry = self._list.get(key)
        if entry is None:
            return None
        path = self._root / f"{key}.img"
        if path.is_file():
            body = await asyncio.to_thread(path.read_bytes)
            kind = _media_type(body)
            if kind:
                return body, kind
        async with self._lock:  # one download at a time; Commons asks to be gentle
            if path.is_file():
                body = await asyncio.to_thread(path.read_bytes)
                kind = _media_type(body)
                if kind:
                    return body, kind
            try:
                r = await self._client.get(entry["url"], timeout=TIMEOUT_S, follow_redirects=True)
                r.raise_for_status()
            except httpx.HTTPError as exc:
                log.info("sky photo failed", extra={"key": key, "error": str(exc)})
                return None
            body = r.content
            kind = _media_type(body)
            if kind is None or len(body) > MAX_BYTES:
                return None
            await asyncio.to_thread(self._write, path, body)
            return body, kind

    @staticmethod
    def _write(path: Path, body: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(body)
        tmp.replace(path)
