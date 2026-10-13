"""Map tiles and place search for the camera map (roadmap C5), through the hub.

Visitors' browsers never talk to a map service: the hub fetches each tile once, keeps it
on disk for ``MAX_AGE`` and serves it from there, so the services see the hub, never a
visitor (as with the aurora map, devices/space_weather.py). The same holds for the place
search (Nominatim), which is also held to its one request per second.

Cameras stand on the map at 0.1° (about 10 km), so the map does not zoom in further than
``MAX_ZOOM``: a street-level view would pretend a precision the positions do not have.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import httpx

log = logging.getLogger(__name__)

MAX_AGE_S = 30 * 86400
MAX_BYTES = 1_000_000
UPSTREAM_AT_ONCE = 4
TIMEOUT_S = 10.0


@dataclass(frozen=True)
class Layer:
    url: str  # {z} {x} {y}
    max_zoom: int
    media_type: str
    credit: str


LAYERS = {
    "karte": Layer(
        "https://tile.openstreetmap.org/{z}/{x}/{y}.png", 11, "image/png",
        '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>-Mitwirkende',
    ),
    "satellit": Layer(
        "https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless_3857/default/g/{z}/{y}/{x}.jpg",
        11, "image/jpeg",
        '<a href="https://s2maps.eu">Sentinel-2 cloudless</a> von EOX (CC BY 4.0, '
        "enthält Copernicus-Sentinel-Daten 2016)",
    ),
    "nacht": Layer(
        "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/VIIRS_Black_Marble/default/"
        "2016-01-01/GoogleMapsCompatible_Level8/{z}/{y}/{x}.png",
        8, "image/png",
        "Nachtlichter: NASA Black Marble 2016 (VIIRS)",
    ),
}  # fmt: skip
MAX_ZOOM = max(layer.max_zoom for layer in LAYERS.values())

SEARCH_URL = "https://nominatim.openstreetmap.org/search"
SEARCH_EVERY_S = 1.0  # Nominatim's usage policy
SEARCH_CACHE_S = 86400.0
SEARCH_MAX = 5


class MapTiles:
    def __init__(self, root: Path, client: httpx.AsyncClient) -> None:
        self._root = root
        self._client = client
        self._upstream = asyncio.Semaphore(UPSTREAM_AT_ONCE)
        self._locks: dict[str, asyncio.Lock] = {}
        self._search_lock = asyncio.Lock()
        self._last_search = 0.0
        self._searches: dict[str, tuple[float, list[dict[str, Any]]]] = {}

    def _path(self, layer: str, z: int, x: int, y: int) -> Path:
        suffix = ".jpg" if LAYERS[layer].media_type == "image/jpeg" else ".png"
        return self._root / layer / str(z) / str(x) / f"{y}{suffix}"

    async def tile(self, layer: str, z: int, x: int, y: int) -> bytes | None:
        """The tile, from the disk cache or fetched once; None for an unknown or
        too-deep tile, or when the service fails and nothing is cached."""
        found = LAYERS.get(layer)
        if found is None or not 0 <= z <= found.max_zoom or not (0 <= x < 2**z and 0 <= y < 2**z):
            return None
        path = self._path(layer, z, x, y)
        fresh = await asyncio.to_thread(_read_fresh, path)
        if fresh is not None:
            return fresh
        key = f"{layer}/{z}/{x}/{y}"
        lock = self._locks.setdefault(key, asyncio.Lock())
        try:
            async with lock:
                fresh = await asyncio.to_thread(_read_fresh, path)
                if fresh is not None:
                    return fresh
                try:
                    async with self._upstream:
                        r = await self._client.get(
                            found.url.format(z=z, x=x, y=y), timeout=TIMEOUT_S
                        )
                    r.raise_for_status()
                    body = r.content
                except httpx.HTTPError as exc:
                    log.info("map tile failed", extra={"tile": key, "error": str(exc)})
                    return await asyncio.to_thread(_read_any, path)  # stale beats nothing
                if len(body) > MAX_BYTES or not body.startswith((b"\x89PNG", b"\xff\xd8")):
                    return await asyncio.to_thread(_read_any, path)
                await asyncio.to_thread(_write, path, body)
                return body
        finally:
            if not lock.locked():
                self._locks.pop(key, None)

    async def search(self, query: str) -> list[dict[str, Any]]:
        """Places for ``query``: ``{name, lat, lon, bbox: [south, north, west, east]}``."""
        q = " ".join(query.split())[:100]
        if len(q) < 2:
            return []
        key = q.lower()
        cached = self._searches.get(key)
        if cached and time.monotonic() - cached[0] < SEARCH_CACHE_S:
            return cached[1]
        async with self._search_lock:
            wait = self._last_search + SEARCH_EVERY_S - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_search = time.monotonic()
            try:
                r = await self._client.get(
                    SEARCH_URL,
                    params={
                        "q": q,
                        "format": "jsonv2",
                        "limit": SEARCH_MAX,
                        "accept-language": "de",
                    },
                    timeout=TIMEOUT_S,
                )
                r.raise_for_status()
                raw: object = r.json()
            except (httpx.HTTPError, json.JSONDecodeError) as exc:
                log.info("place search failed", extra={"error": str(exc)})
                return []
        places = _places(raw)
        if len(self._searches) > 2000:
            self._searches.clear()
        self._searches[key] = (time.monotonic(), places)
        return places


def _places(raw: object) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return out
    for item in cast(list[object], raw):
        if not isinstance(item, dict):
            continue
        entry = cast(dict[str, Any], item)
        try:
            lat, lon = float(entry["lat"]), float(entry["lon"])
            raw_box: object = entry.get("boundingbox") or []
            box = [float(v) for v in cast(list[Any], raw_box)] if isinstance(raw_box, list) else []
        except (KeyError, TypeError, ValueError):
            continue
        out.append({
            "name": str(entry.get("display_name") or "")[:200],
            "lat": lat,
            "lon": lon,
            "bbox": box if len(box) == 4 else None,
        })  # fmt: skip
    return out


def _read_fresh(path: Path) -> bytes | None:
    try:
        if time.time() - path.stat().st_mtime > MAX_AGE_S:
            return None
        return path.read_bytes()
    except OSError:
        return None


def _read_any(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def _write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(body)
    tmp.replace(path)
