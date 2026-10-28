"""Photos of sky objects for the app (allskyhub_sky ``photos.json``), fetched once from
Wikimedia Commons by the hub and kept on disk, so the app talks only to the hub and
Commons learns nothing about its users. Only freely licensed files are listed there
(tools/sky_photos.py); the app shows each one's author and licence.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import httpx

from allskyhub_sky.describe import photos

log = logging.getLogger(__name__)

TIMEOUT_S = 20.0
MAX_BYTES = 4_000_000
# A photo that could not be fetched is not tried again for this long.
RETRY_S = 3600.0


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
        self._failed: dict[str, float] = {}

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
        failed = self._failed.get(key)
        if failed is not None and time.monotonic() - failed < RETRY_S:
            return None
        async with self._lock:  # one download at a time; Commons asks to be gentle
            if path.is_file():
                body = await asyncio.to_thread(path.read_bytes)
                kind = _media_type(body)
                if kind:
                    return body, kind
            body = await self._fetch(key, entry["url"])
            kind = _media_type(body) if body is not None else None
            if body is None or kind is None:
                self._failed[key] = time.monotonic()
                return None
            self._failed.pop(key, None)
            await asyncio.to_thread(self._write, path, body)
            return body, kind

    async def _fetch(self, key: str, url: str) -> bytes | None:
        """The file, or None when it fails or grows past ``MAX_BYTES`` (read in chunks,
        so an oversized answer is never held whole)."""
        try:
            async with self._client.stream(
                "GET", url, timeout=TIMEOUT_S, follow_redirects=True
            ) as r:
                r.raise_for_status()
                if int(r.headers.get("content-length") or 0) > MAX_BYTES:
                    return None
                chunks: list[bytes] = []
                size = 0
                async for chunk in r.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        return None
                    chunks.append(chunk)
                return b"".join(chunks)
        except (httpx.HTTPError, ValueError) as exc:  # ValueError: a broken Content-Length
            log.info("sky photo failed", extra={"key": key, "error": str(exc)})
            return None

    @staticmethod
    def _write(path: Path, body: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(body)
        tmp.replace(path)
