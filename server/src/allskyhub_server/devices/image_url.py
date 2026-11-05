"""Stations by their image address (SharedSky, SPEC §10.7).

Most cameras publish their latest image at a fixed address. Such a station is asked for on
"Kamera verbinden" with the address, the place and a proof; an admin checks it by hand
(web/routes_stations.py); only then does the hub fetch the image, every 30 s at first and
then at half the interval the image usually changes in (30 s to 5 min).

Fetching never goes anywhere but the public internet: the name is resolved here, every
address must be public (not loopback, private, link-local, multicast, reserved or the
hub's own), and the connection goes to exactly the address checked (pinned; the TLS name
and ``Host`` stay the name), so a second lookup cannot lead elsewhere (DNS rebinding).
Each of at most 3 redirects is checked and pinned the same way. Only ports 80 and 443,
only JPEG (8 MB, 50 MP) or PNG (8 MB, 16 MP), 15 s. Each fetch uses its own connection,
so two stations on one address never share a TLS session.

A new image is a frame of SPEC §10.2 (api/connect.py ``store_frame``): its time from the
EXIF data, else the station's time zone, else ``Last-Modified``, else the fetch; the hub
judges the clock as for every station, and a station whose times come only from the fetch
is never trusted for pairing.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import email.utils
import hashlib
import io
import ipaddress
import itertools
import logging
import socket
import statistics
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import SplitResult, urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices import station_detect
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.mail.send import Mail, Mailer
from allskyhub_server.models import Device, StationSource, User
from allskyhub_sky.astro import sun_altitude

log = logging.getLogger(__name__)

USER_AGENT = "SharedSky (+https://allskyhub.org/mitmachen)"
PORTS = {"http": 80, "https": 443}
MAX_BYTES = 8 * 1024 * 1024
JPEG_MAX_PIXELS = 50_000_000
PNG_MAX_PIXELS = 16_000_000
TIMEOUT_S = 15.0
MAX_REDIRECTS = 3
MIN_INTERVAL_S, MAX_INTERVAL_S = 30, 300
CHANGES_KEPT = 10
BACKOFF_START_S, BACKOFF_MAX_S = 60, 3600
PAUSE_AFTER = dt.timedelta(hours=24)
TICK_S = 5.0
PER_ROUND = 20
AT_ONCE = 4
# Orientation that fails on clear frames this many nights sends the station back (§10.7).
FAILED_NIGHTS = 3
# Proof pictures on the request (re-encoded, no metadata): at most this big.
PROOF_MAX_UPLOAD = 5 * 1024 * 1024
PROOF_EDGE = 1600
# A proof picture needs no more; decoding stays small (allsky's review of #399).
PROOF_MAX_PIXELS = 25_000_000
# The overlay mask (§10.7): a pixel that changed in this part of the frames is overlay.
MASK_SIDE = 320
MASK_CHANGE = 20
MASK_SHARE = 0.8
MASK_MIN_FRAMES = 20

ERRORS = {
    "bad_url": (
        "Die Adresse ist ungültig (nur http oder https, Port 80 oder 443, ohne Zugangsdaten)."
    ),
    "not_public": "Die Adresse führt nicht ins öffentliche Internet.",
    "unreachable": "Der Server ist nicht erreichbar.",
    "timeout": "Der Server antwortet nicht rechtzeitig.",
    "wrong_type": "Dort liegt kein JPEG- oder PNG-Bild.",
    "too_large": "Das Bild ist zu groß (höchstens 8 MB, 50 MP bei JPEG, 16 MP bei PNG).",
    "bad_image": "Das Bild lässt sich nicht lesen.",
    "too_many_redirects": "Zu viele Weiterleitungen.",
    "insecure_redirect": "Die Adresse leitet von https auf unverschlüsseltes http um.",
    "busy": "Der Server bittet, später wiederzukommen.",
    "http": "Der Server antwortet mit einem Fehler.",
}


class FetchError(Exception):
    def __init__(self, code: str, detail: str = "", retry_after: int | None = None) -> None:
        super().__init__(f"{code} {detail}".strip())
        self.code = code
        self.detail = detail
        self.retry_after = retry_after

    @property
    def text(self) -> str:
        return ERRORS.get(self.code, self.code) + (f" ({self.detail})" if self.detail else "")


Resolver = Callable[[str, int], Awaitable[list[str]]]


async def system_resolve(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    seen: list[str] = []
    for *_, sockaddr in infos:
        ip = str(sockaddr[0])
        if ip not in seen:
            seen.append(ip)
    return seen


def check_url(url: str) -> SplitResult:
    """http or https, a name, port 80 or 443 (explicit or not), no credentials."""
    if len(url) > 2048:
        raise FetchError("bad_url")
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        raise FetchError("bad_url") from None
    if parts.scheme not in PORTS or not parts.hostname or parts.username or parts.password:
        raise FetchError("bad_url")
    if port is not None and port not in PORTS.values():
        raise FetchError("bad_url")
    return parts


NAT64 = ipaddress.IPv6Network("64:ff9b::/96")


def public(ip: str, own: frozenset[str]) -> bool:
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address):
        # IPv4 inside IPv6 is checked as the IPv4 it reaches: mapped (::ffff:0:0/96), NAT64
        # (64:ff9b::/96, global to Python but private behind a NAT64) and 6to4 (2002::/16).
        embedded = address.ipv4_mapped or address.sixtofour
        if address in NAT64:
            embedded = ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
        if embedded is not None:
            address = embedded
    return (
        address.is_global
        and not address.is_multicast
        and not address.is_reserved
        and str(address) not in own
    )


async def pinned_address(host: str, port: int, resolve: Resolver, own: frozenset[str]) -> str:
    """The one address to connect to: every address of the name must be public."""
    try:
        addresses = [host] if _is_ip(host) else await resolve(host, port)
    except OSError as exc:
        raise FetchError("unreachable", "Name unbekannt") from exc
    if not addresses or not all(public(ip, own) for ip in addresses):
        raise FetchError("not_public")
    return addresses[0]


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class Fetched:
    new: bool  # False: unchanged (304)
    data: bytes = b""
    content_type: str = ""
    etag: str | None = None
    last_modified: str | None = None


class Fetcher:
    """GET of an image address, pinned to the checked address (module docstring)."""

    def __init__(
        self,
        resolve: Resolver = system_resolve,
        own: frozenset[str] = frozenset(),
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._resolve = resolve
        self._own = own
        self._transport = transport

    async def get(
        self, url: str, etag: str | None = None, last_modified: str | None = None
    ) -> Fetched:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            parts = check_url(current)
            host = parts.hostname or ""
            port = parts.port or PORTS[parts.scheme]
            ip = await pinned_address(host, port, self._resolve, self._own)
            netloc = f"[{ip}]:{port}" if ":" in ip else f"{ip}:{port}"
            target = urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))
            headers = {
                "Host": host if parts.port is None else f"{host}:{port}",
                "User-Agent": USER_AGENT,
                "Accept": "image/jpeg, image/png",
            }
            if etag:
                headers["If-None-Match"] = etag
            if last_modified:
                headers["If-Modified-Since"] = last_modified
            extensions = {"sni_hostname": host} if parts.scheme == "https" else {}
            transport = self._transport or httpx.AsyncHTTPTransport(retries=0)
            try:
                async with httpx.AsyncClient(
                    transport=transport, trust_env=False, timeout=TIMEOUT_S
                ) as client:
                    request = client.build_request(
                        "GET", target, headers=headers, extensions=extensions
                    )
                    response = await client.send(request, stream=True)
                    try:
                        found = await _read(response)
                    finally:
                        await response.aclose()
            except httpx.TimeoutException as exc:
                raise FetchError("timeout") from exc
            except httpx.HTTPError as exc:
                raise FetchError("unreachable", type(exc).__name__) from exc
            if isinstance(found, str):
                following = urljoin(current, found)
                # An address given with https never falls back to plain http.
                if urlsplit(url).scheme == "https" and urlsplit(following).scheme != "https":
                    raise FetchError("insecure_redirect")
                current = following
                continue
            return found
        raise FetchError("too_many_redirects")


async def _read(response: httpx.Response) -> Fetched | str:
    """The image, "unchanged", or the next address (a redirect)."""
    status = response.status_code
    if status in (301, 302, 303, 307, 308):
        location = response.headers.get("location")
        if not location:
            raise FetchError("http", str(status))
        return location
    if status == 304:
        return Fetched(False)
    if status in (429, 503):
        raise FetchError("busy", str(status), _retry_after(response.headers.get("retry-after")))
    if status != 200:
        raise FetchError("http", str(status))
    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type not in ("image/jpeg", "image/png"):
        raise FetchError("wrong_type", content_type or "?")
    length = response.headers.get("content-length", "")
    if length.isdigit() and int(length) > MAX_BYTES:
        raise FetchError("too_large")
    buffer = bytearray()
    async for chunk in response.aiter_bytes():
        buffer += chunk
        if len(buffer) > MAX_BYTES:
            raise FetchError("too_large")
    return Fetched(
        True,
        bytes(buffer),
        content_type,
        response.headers.get("etag"),
        response.headers.get("last-modified"),
    )


def _retry_after(value: str | None) -> int | None:
    if not value:
        return None
    if value.strip().isdigit():
        return min(int(value), BACKOFF_MAX_S)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    seconds = (when - dt.datetime.now(dt.UTC)).total_seconds()
    return max(0, min(int(seconds), BACKOFF_MAX_S))


@dataclass(frozen=True)
class Prepared:
    jpeg: bytes  # as fetched, or the PNG re-encoded
    width: int
    height: int
    exif_time: str | None  # DateTimeOriginal "YYYY:MM:DD HH:MM:SS"
    exif_offset: str | None  # OffsetTimeOriginal "+01:00"


def prepare(data: bytes, content_type: str) -> Prepared:
    """Check the image (its size from the header before decoding), read its EXIF time, and
    make a PNG a JPEG for the frame path of SPEC §10.2."""
    try:
        im = Image.open(io.BytesIO(data))
    except (UnidentifiedImageError, OSError) as exc:
        raise FetchError("bad_image") from exc
    with im:
        expected = "PNG" if content_type == "image/png" else "JPEG"
        if im.format != expected:
            raise FetchError("bad_image", f"{im.format} statt {expected}")
        limit = PNG_MAX_PIXELS if expected == "PNG" else JPEG_MAX_PIXELS
        if im.size[0] * im.size[1] > limit:
            raise FetchError("too_large")
        exif = im.getexif()
        sub = exif.get_ifd(0x8769)
        taken = sub.get(36867) or exif.get(306)
        offset = sub.get(36881)
        width, height = im.size
        if expected == "PNG":
            try:
                out = io.BytesIO()
                im.convert("RGB").save(out, "JPEG", quality=92)
            except (OSError, ValueError, Image.DecompressionBombError) as exc:
                raise FetchError("bad_image") from exc
            data = out.getvalue()
    return Prepared(
        data,
        width,
        height,
        str(taken) if taken else None,
        str(offset) if offset else None,
    )


def frame_time(
    prepared: Prepared, zone: str | None, last_modified: str | None, fetched: dt.datetime
) -> tuple[dt.datetime, str]:
    """SPEC §10.7 Time: the moment and where it came from."""
    if prepared.exif_time:
        try:
            # EXIF has no zone: the offset or the station's zone gives it below.
            naive = dt.datetime.strptime(prepared.exif_time.strip(), "%Y:%m:%d %H:%M:%S")  # noqa: DTZ007
        except ValueError:
            naive = None
        if naive is not None:
            if prepared.exif_offset:
                try:
                    aware = dt.datetime.fromisoformat(
                        naive.isoformat() + prepared.exif_offset.strip()
                    )
                    return aware.astimezone(dt.UTC), "exif"
                except ValueError:
                    pass
            if zone:
                try:
                    return naive.replace(tzinfo=ZoneInfo(zone)).astimezone(dt.UTC), "exif_zone"
                except (ZoneInfoNotFoundError, ValueError):
                    pass
    if last_modified:
        try:
            when = email.utils.parsedate_to_datetime(last_modified)
            if when.tzinfo is not None:
                return when.astimezone(dt.UTC), "last_modified"
        except (TypeError, ValueError):
            pass
    return fetched, "fetch"


def next_interval(changes: list[float]) -> int:
    """Half the usual interval between changes, 30 s to 5 min (SPEC §10.7)."""
    gaps = [b - a for a, b in itertools.pairwise(changes) if b > a]
    if len(gaps) < 2:
        return MIN_INTERVAL_S
    half = statistics.median(gaps) / 2
    return int(max(MIN_INTERVAL_S, min(MAX_INTERVAL_S, half)))


def backoff(errors: int) -> int:
    """1 min, doubling, at most 1 h (SPEC §10.7)."""
    return min(BACKOFF_START_S * 2 ** max(errors - 1, 0), BACKOFF_MAX_S)


# The overlay mask (SPEC §10.7): pixels that change in nearly every frame are text such as
# the time and the exposure, not sky.


def _gray(jpeg: bytes) -> np.ndarray[Any, np.dtype[np.uint8]]:
    with Image.open(io.BytesIO(jpeg)) as im:
        im.draft("L", (MASK_SIDE, MASK_SIDE))
        gray = im.convert("L")
        gray.thumbnail((MASK_SIDE, MASK_SIDE))
        raw = gray.tobytes()
        return np.ndarray((gray.height, gray.width), dtype=np.uint8, buffer=raw).copy()


class OverlayMask:
    """Counts per station of how often each pixel of a reduced frame changed, kept in
    ``<root>/<device id>.npz``."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, device_id: str) -> Path:
        return self.root / f"{device_id}.npz"

    def update(self, device_id: str, jpeg: bytes) -> None:
        gray = _gray(jpeg)
        path = self._path(device_id)
        counts = np.zeros(gray.shape, dtype=np.uint16)
        frames, last = 0, None
        if path.is_file():
            with np.load(path) as kept:
                if kept["last"].shape == gray.shape:
                    counts, last = kept["counts"], kept["last"]
                    frames = int(kept["frames"])
        if last is not None:
            changed = np.abs(gray.astype(np.int16) - last.astype(np.int16)) > MASK_CHANGE
            counts = np.minimum(counts + changed.astype(np.uint16), 60000).astype(np.uint16)
            frames = min(frames + 1, 60000)
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp.npz")
        np.savez(tmp, counts=counts, last=gray, frames=np.array(frames))  # pyright: ignore[reportUnknownMemberType]
        tmp.replace(path)

    def mask(self, device_id: str) -> np.ndarray[Any, np.dtype[np.bool_]] | None:
        """True where the frame is overlay; None until enough frames were seen."""
        path = self._path(device_id)
        if not path.is_file():
            return None
        with np.load(path) as kept:
            frames = int(kept["frames"])
            if frames < MASK_MIN_FRAMES:
                return None
            share = kept["counts"].astype(np.float32) / float(frames)
            return np.greater(share, MASK_SHARE)

    def forget(self, device_id: str) -> None:
        self._path(device_id).unlink(missing_ok=True)


def store_proof(root: Path, device_id: str, data: bytes) -> None:
    """A proof picture, re-encoded as JPEG without metadata (like the aurora photos)."""
    if len(data) > PROOF_MAX_UPLOAD:
        raise FetchError("too_large")
    try:
        with Image.open(io.BytesIO(data)) as img:
            if img.format not in ("JPEG", "PNG", "WEBP", "MPO"):
                raise FetchError("bad_image")
            if img.size[0] * img.size[1] > PROOF_MAX_PIXELS:
                raise FetchError("too_large")
            img.draft("RGB", (PROOF_EDGE, PROOF_EDGE))  # JPEG decodes at about the size kept
            img.load()
            picture = ImageOps.exif_transpose(img).convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise FetchError("bad_image") from exc
    picture.thumbnail((PROOF_EDGE, PROOF_EDGE))
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / f"{device_id}.tmp"
    picture.save(tmp, "JPEG", quality=85)
    tmp.replace(proof_path(root, device_id))


def proof_path(root: Path, device_id: str) -> Path:
    return root / f"{device_id}.jpg"


def thumbnail(jpeg: bytes, edge: int = 900) -> bytes:
    with Image.open(io.BytesIO(jpeg)) as im:
        im.draft("RGB", (edge, edge))
        small = im.convert("RGB")
        small.thumbnail((edge, edge))
        out = io.BytesIO()
        small.save(out, "JPEG", quality=80)
        return out.getvalue()


async def preview(fetcher: Fetcher, url: str) -> tuple[bytes | None, str]:
    """The image at ``url`` once, for the admin's check: a thumbnail or the error."""
    try:
        found = await fetcher.get(url)
        prepared = await asyncio.to_thread(prepare, found.data, found.content_type)
        return await asyncio.to_thread(thumbnail, prepared.jpeg), ""
    except FetchError as exc:
        return None, exc.text


def own_addresses(base_url: str) -> frozenset[str]:
    """The hub's own addresses, from its name (never fetched)."""
    host = urlsplit(base_url).hostname
    if not host:
        return frozenset()
    try:
        return frozenset(str(info[4][0]) for info in socket.getaddrinfo(host, None))
    except OSError:
        return frozenset()


# Fetching (the loop in the app's lifespan).


@dataclass
class Context:
    maker: async_sessionmaker[AsyncSession]
    store: ImageStore
    mailer: Mailer
    base_url: str
    fetcher: Fetcher
    masks: OverlayMask
    detector: station_detect.Detector


async def _mail_owner(
    ctx: Context, db: AsyncSession, device: Device, subject: str, body: str
) -> None:
    owner = await db.get(User, device.owner_id) if device.owner_id else None
    if owner is None or not ctx.mailer.enabled:
        return
    await ctx.mailer.send(Mail(to=owner.email, subject=subject, body=body))


def _station_page(ctx: Context, device: Device) -> str:
    return f"{ctx.base_url.rstrip('/')}/stationen/{device.id}"


async def fetch_one(ctx: Context, device_id: str, now: dt.datetime) -> None:
    async with ctx.maker() as db:
        source = await db.get(StationSource, device_id)
        device = await db.get(Device, device_id)
        if source is None or device is None or source.state != "approved":
            return
        if await _failed_orientation(ctx, db, device, source):
            await db.commit()
            return
        try:
            found = await ctx.fetcher.get(source.url, source.etag, source.last_modified)
        except FetchError as exc:
            source.errors += 1
            since = source.errors_since or now
            source.errors_since = since
            source.last_error = exc.text[:200]
            wait = exc.retry_after if exc.retry_after is not None else backoff(source.errors)
            source.next_fetch_at = now + dt.timedelta(seconds=max(wait, MIN_INTERVAL_S))
            if now - since >= PAUSE_AFTER:
                source.state = "paused"
                source.reason = f"Seit 24 Stunden nicht abrufbar: {exc.text}"[:500]
                await _mail_owner(
                    ctx, db, device, f"SharedSky: {device.name} pausiert",
                    "Hallo,\n\ndas Bild deiner Station ist seit einem Tag nicht abrufbar:\n\n"
                    f"{source.url}\n{exc.text}\n\nDie Station ist pausiert. Sobald es wieder "
                    f"geht, setze sie hier fort:\n\n{_station_page(ctx, device)}\n\nallskyhub\n",
                )  # fmt: skip
            await db.commit()
            return
        source.errors, source.errors_since, source.last_error = 0, None, ""
        if found.new:
            await _process(ctx, db, device, source, found, now)
        source.next_fetch_at = now + dt.timedelta(seconds=source.interval_s)
        await db.commit()


async def _process(
    ctx: Context,
    db: AsyncSession,
    device: Device,
    source: StationSource,
    found: Fetched,
    now: dt.datetime,
) -> None:
    from allskyhub_server.api import connect  # the frame path of SPEC §10.2

    source.etag, source.last_modified = found.etag, found.last_modified
    try:
        prepared = await asyncio.to_thread(prepare, found.data, found.content_type)
    except FetchError as exc:
        source.last_error = exc.text[:200]
        return
    digest = hashlib.sha256(prepared.jpeg).digest()
    if digest == source.content_hash:
        return  # the same bytes again (a server without ETag or date)
    source.content_hash = digest
    source.changes = [*list(source.changes or [])[-(CHANGES_KEPT - 1) :], now.timestamp()]
    source.interval_s = next_interval(source.changes)
    _apply_size(device, prepared.width, prepared.height)
    settings: dict[str, Any] = (device.last_status or {}).get("settings") or {}
    taken, origin = frame_time(prepared, settings.get("timezone"), found.last_modified, now)
    lat, lon = settings.get("latitude"), settings.get("longitude")
    sun = (
        sun_altitude(taken, float(lat), float(lon))
        if isinstance(lat, int | float) and isinstance(lon, int | float)
        else 0.0
    )
    meta = connect.FrameIn(
        time=taken, exposure_s=0.0, night=sun <= -6.0, clock_synced=origin != "fetch"
    )
    try:
        await connect.store_frame(
            db, ctx.store, ctx.maker, device, meta, prepared.jpeg, full_always=True
        )
    except Exception as exc:  # StationError and a broken frame alike: the next one may do
        source.last_error = str(exc)[:200]
        return
    device.last_frame = dict(device.last_frame or {}) | {"time_source": origin}
    if origin == "fetch":
        clock = dict(device.clock or {})
        clock["trusted"] = False  # SPEC §10.7: never trusted for pairing
        device.clock = clock
    await asyncio.to_thread(ctx.masks.update, device.id, prepared.jpeg)
    # Meteors (SPEC §10.7): the camera's detection on runs of frames; found ones are in the
    # frame before this one and wait for a trial before they are paired.
    overlay = await asyncio.to_thread(ctx.masks.mask, device.id)
    meteors = await ctx.detector.feed(
        device.id, prepared.jpeg, taken, 0.0, origin, usual_s=source.interval_s * 2.0,
        overlay=overlay, orientation=(device.last_status or {}).get("orientation"),
    )  # fmt: skip
    if meteors is not None:
        await station_detect.store_found(db, ctx.store, device, meteors, "image_url")


def _apply_size(device: Device, width: int, height: int) -> None:
    """The frame size from the image itself; a new size drops the orientation (§10.2)."""
    status: dict[str, Any] = dict(device.last_status or {})
    info: dict[str, Any] = dict(status.get("station") or {})
    if (info.get("width"), info.get("height")) != (width, height):
        info["width"], info["height"] = width, height
        status["station"] = info
        status["orientation"] = None
        device.last_status = status


async def _failed_orientation(
    ctx: Context, db: AsyncSession, device: Device, source: StationSource
) -> bool:
    """SPEC §10.7: the orientation failed on clear frames ``FAILED_NIGHTS`` nights: back to
    the admin."""
    solve: dict[str, Any] = (device.last_status or {}).get("solve") or {}
    fails: list[Any] = solve.get("fails") or []
    if len(fails) < FAILED_NIGHTS:
        return False
    source.state = "pending"
    source.reason = (
        "Das Bild passt nicht mehr zum Ort: die Ausrichtung an den Sternen schlug drei "
        "klare Nächte fehl."
    )
    status = dict(device.last_status or {})
    status["solve"] = {**solve, "fails": []}
    device.last_status = status
    await _mail_owner(
        ctx, db, device, f"SharedSky: {device.name} wird neu geprüft",
        "Hallo,\n\ndas Bild deiner Station passt in drei klaren Nächten nicht zu ihrem Ort: "
        "Die Ausrichtung an den Sternen schlug fehl. Hat sich die Kamera oder die Adresse "
        "geändert? Ein Admin prüft die Station neu; bis dahin holt der Hub nichts ab.\n\n"
        f"{_station_page(ctx, device)}\n\nallskyhub\n",
    )  # fmt: skip
    return True


async def round_once(ctx: Context, now: dt.datetime) -> int:
    """Fetch the approved stations that are due; returns how many."""
    async with ctx.maker() as db:
        due = list(
            await db.scalars(
                select(StationSource.device_id)
                .where(
                    StationSource.state == "approved",
                    or_(StationSource.next_fetch_at.is_(None), StationSource.next_fetch_at <= now),
                )
                .order_by(StationSource.next_fetch_at.nulls_first())
                .limit(PER_ROUND)
            )
        )
    gate = asyncio.Semaphore(AT_ONCE)

    async def one(device_id: str) -> None:
        async with gate:
            try:
                await fetch_one(ctx, device_id, now)
            except Exception:  # one station must not stop the others
                log.exception("image address fetch failed", extra={"device_id": device_id})

    await asyncio.gather(*(one(d) for d in due))
    return len(due)


CLEAN_EVERY_S = 600.0


async def run_forever(ctx: Context) -> None:
    cleaned = 0.0
    while True:
        try:
            await round_once(ctx, dt.datetime.now(dt.UTC))
            if time.monotonic() - cleaned > CLEAN_EVERY_S:  # detection buffers of silent stations
                cleaned = time.monotonic()
                await asyncio.to_thread(ctx.detector.buffer.clean)
        except Exception:
            log.exception("image address round failed")
        await asyncio.sleep(TICK_S)
