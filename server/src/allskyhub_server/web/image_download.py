"""The camera's SD card image for /start and /download/image.

Every agent release on GitHub carries the image (``allskyhub-<version>.img.xz``). The hub
asks GitHub for the latest release at most once an hour (anonymous API, 60 requests an
hour) and points the download at that file, so links in posts and on the wizard never go
stale. When GitHub cannot be reached it falls back to the releases page.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, cast

import httpx

API = "https://api.github.com"
CACHE_S = 3600.0
FAILED_CACHE_S = 300.0
IMAGE = re.compile(r"^allskyhub-(?P<version>[0-9][0-9A-Za-z.+-]*)\.img\.xz$")


@dataclass(frozen=True)
class Image:
    version: str
    url: str
    size_mb: int


class LatestImage:
    def __init__(self, repo: str, client: httpx.AsyncClient | None = None) -> None:
        self.repo = repo
        self.releases_url = f"https://github.com/{repo}/releases/latest"
        self._client = client
        self._cached: tuple[float, Image | None] | None = None

    async def get(self) -> Image | None:
        now = time.monotonic()
        if self._cached is not None:
            at, image = self._cached
            if now - at < (CACHE_S if image else FAILED_CACHE_S):
                return image
        image = await self._fetch()
        self._cached = (now, image)
        return image

    async def _fetch(self) -> Image | None:
        client = self._client or httpx.AsyncClient(timeout=5)
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "allskyhub-hub"}
        try:
            r = await client.get(f"{API}/repos/{self.repo}/releases/latest", headers=headers)
            if r.status_code != 200:
                return None
            body = r.json()
            raw = cast(dict[str, Any], body).get("assets") if isinstance(body, dict) else None
            assets = cast(list[object], raw) if isinstance(raw, list) else []
            for item in assets:
                if not isinstance(item, dict):
                    continue
                asset = cast(dict[str, Any], item)
                name = str(asset.get("name", ""))
                found = IMAGE.match(name)
                url = asset.get("browser_download_url")
                if found and isinstance(url, str) and url.startswith("https://github.com/"):
                    size = asset.get("size")
                    mb = round(size / 1e6) if isinstance(size, int) else 0
                    return Image(found["version"], url, mb)
            return None
        except (httpx.HTTPError, ValueError):
            return None
        finally:
            if self._client is None:
                await client.aclose()
