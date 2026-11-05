"""Connected cameras, "stations" (SharedSky, SPEC §10).

A station is a ``Device`` row with ``kind = "station"``: cameras with other software
(indi-allsky, Allsky, own code) that send over HTTPS (api/connect.py, api/indi_allsky.py)
instead of the device protocol (§6). Its id is a device id; the device's ``public_key``
is random bytes no one holds, so the device API never lets it in.

Its secret (64 hex characters) is shown once. For ``connect/v1`` only its SHA-256 is kept;
an indi-allsky station's secret is also kept encrypted (AES-GCM with the vault's
``station_key``), because indi-allsky signs with it (§10.3).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import io
import itertools
import logging
import math
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from PIL import Image
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from allskyhub_protocol import device_id_from_public_key
from allskyhub_server.models import (
    CameraMember,
    Device,
    EventRecord,
    Frame,
    RenderJob,
    StatusSample,
    User,
)

log = logging.getLogger(__name__)

SOFTWARE = ("indi-allsky", "allsky", "other")
KEY_KIND = "station_key"  # in the vault (admin/stores.py), made at the hub's start
# SPEC §10.1 Clock.
CLOCK_EARLY_S = 120.0  # plus the exposure: a frame this much older than its arrival
CLOCK_LATE_S = 60.0  # a frame from this far in the future is always wrong
CLOCK_BAD_IN_A_ROW = 3
CLOCK_GOOD_IN_A_ROW = 10
# SPEC §10.1 Limits and keeping.
EVENTS_PER_NIGHT = 3000
FRAME_EVERY = dt.timedelta(minutes=1)
MAX_BYTES = 2 * 1024**3
THUMB_SIDE = 640
# A frame larger than this is refused before it is decoded (a JPEG's size is in its header).
MAX_PIXELS = 50_000_000


class StationError(Exception):
    """An answer of the open API (SPEC §10.2 errors): status, code and, for
    ``bad_request``, the field."""

    def __init__(
        self, status: int, code: str, field: str | None = None, retry_after: int | None = None
    ) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.field = field
        self.retry_after = retry_after


class SecretKey:
    """The vault's key for indi-allsky stations' secrets (32 random bytes). ``ensure``
    makes it once: at the hub's start, and safely if two make it at the same time."""

    def __init__(self, vault: Any) -> None:
        self._vault = vault

    def ensure(self) -> None:
        if not (Path(self._vault.root) / f"{KEY_KIND}.secret").exists():
            self._vault.put_if_absent(KEY_KIND, os.urandom(32), {}, by="hub")

    def _key(self) -> bytes:
        self.ensure()
        return bytes(self._vault.secret(KEY_KIND, "station secret"))

    def seal(self, secret: str) -> bytes:
        nonce = os.urandom(12)
        return nonce + AESGCM(self._key()).encrypt(nonce, secret.encode(), b"station")

    def open(self, sealed: bytes) -> str:
        return AESGCM(self._key()).decrypt(sealed[:12], sealed[12:], b"station").decode()


def hash_secret(secret: str) -> bytes:
    return hashlib.sha256(secret.encode()).digest()


def _new_secret(device: Device, key: SecretKey) -> str:
    secret = secrets.token_hex(32)
    device.station_secret_hash = hash_secret(secret)
    device.station_secret_enc = key.seal(secret) if device.software == "indi-allsky" else None
    return secret


async def create(
    db: AsyncSession, owner: User, name: str, software: str, key: SecretKey
) -> tuple[Device, str]:
    """A new station of ``owner`` and its secret (shown once). Creating one means the owner
    agreed to the terms (SPEC §10.6), so it is in the network."""
    if software not in SOFTWARE:
        raise ValueError(software)
    now = dt.datetime.now(dt.UTC)
    public_key = os.urandom(32)  # no private key exists: the device API never accepts it
    device = Device(
        id=device_id_from_public_key(public_key),
        public_key=public_key,
        owner_id=owner.id,
        name=name.strip()[:100] or "Station",
        profile="station",
        agent_version="",
        paired_at=now,
        kind="station",
        software=software,
        terms_accepted_at=now,
        network=True,
        # SPEC §10.1: a new station's clock is trusted once enough frames showed it.
        clock={"trusted": False, "bad": 0, "good": 0, "recent": []},
    )
    secret = _new_secret(device, key)
    db.add(device)
    await db.flush()
    return device, secret


def rotate_secret(device: Device, key: SecretKey) -> str:
    """A new secret; the old one stops working at once."""
    return _new_secret(device, key)


async def authenticate(db: AsyncSession, station_id: str, secret: str) -> Device:
    """SPEC §10.2: ``Bearer <station id>:<secret>``."""
    device = await db.get(Device, station_id) if len(station_id) == 26 else None
    stored = device.station_secret_hash if device is not None else None
    if (
        device is None
        or device.kind != "station"
        or device.owner_id is None
        or stored is None
        or not hmac.compare_digest(stored, hash_secret(secret))
    ):
        raise StationError(401, "unauthorized")
    return device


def check_clock(device: Device, taken: dt.datetime, exposure_s: float, now: dt.datetime) -> None:
    """SPEC §10.1 Clock: a frame from the future is always wrong; one older than
    ``CLOCK_EARLY_S`` plus its exposure only while the station sends live (its last three
    frames came as far apart as they were taken, within half), so frames sent late after an
    outage do not judge the clock."""
    state: dict[str, Any] = dict(device.clock or {})
    raw: list[Any] = state.get("recent") or []
    recent = [[float(a), float(b)] for a, b in raw][-2:]
    recent.append([taken.timestamp(), now.timestamp()])
    state["recent"] = recent
    age = (now - taken).total_seconds()
    verdict: bool | None = None  # None: this frame does not judge the clock
    if age < -CLOCK_LATE_S:
        verdict = False
    elif age > CLOCK_EARLY_S + exposure_s:
        if _live(recent):
            verdict = False
    else:
        verdict = True
    if verdict is False:
        state["bad"] = int(state.get("bad", 0)) + 1
        state["good"] = 0
        if state["bad"] >= CLOCK_BAD_IN_A_ROW:
            state["trusted"] = False
    elif verdict is True:
        state["good"] = int(state.get("good", 0)) + 1
        state["bad"] = 0
        if state["good"] >= CLOCK_GOOD_IN_A_ROW:
            state["trusted"] = True
    state.setdefault("trusted", False)
    device.clock = state


def _live(recent: list[list[float]]) -> bool:
    if len(recent) < 3:
        return False
    for (t1, a1), (t2, a2) in itertools.pairwise(recent):
        taken, arrived = t2 - t1, a2 - a1
        if arrived <= 0 or abs(taken - arrived) >= arrived / 2:
            return False
    return True


def clock_trusted(device: Device, word: bool) -> bool:
    """Both the station's word and the hub's check (SPEC §10.1)."""
    return word and bool((device.clock or {}).get("trusted", False))


def night_id(taken: dt.datetime, longitude: float | None) -> str:
    """SPEC §4.5 for a station: its local time from the longitude (mean solar time)."""
    local = taken + dt.timedelta(hours=(longitude or 0.0) / 15)
    day = local.date() if local.hour >= 12 else local.date() - dt.timedelta(days=1)
    return day.strftime("%Y%m%d")


def longitude(device: Device) -> float | None:
    settings: dict[str, Any] = (device.last_status or {}).get("settings") or {}
    value = settings.get("longitude")
    return float(value) if isinstance(value, int | float) else None


async def event_id(db: AsyncSession, device: Device, kind: str, start: dt.datetime) -> str:
    """SPEC §6.4: the kind and the UTC start, with -2, -3 … within the same second."""
    base = f"{kind}-{start:%Y%m%dT%H%M%SZ}"
    taken = set(
        await db.scalars(
            select(EventRecord.event_id).where(
                EventRecord.device_id == device.id, EventRecord.event_id.like(f"{base}%")
            )
        )
    )
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


async def night_full(db: AsyncSession, device: Device, night: str) -> bool:
    count = await db.scalar(
        select(func.count())
        .select_from(EventRecord)
        .where(EventRecord.device_id == device.id, EventRecord.night_id == night)
    )
    return (count or 0) >= EVENTS_PER_NIGHT


@dataclass(frozen=True)
class Picture:
    width: int
    height: int
    thumb: bytes  # longest side at most THUMB_SIDE
    mean: float  # 0..255


def open_jpeg(data: bytes) -> Image.Image:
    """A JPEG, its size checked from the header before anything is decoded."""
    try:
        im = Image.open(io.BytesIO(data))
    except Exception as exc:  # Pillow raises many kinds for broken files
        raise StationError(400, "bad_image") from exc
    if im.format != "JPEG":
        im.close()
        raise StationError(400, "bad_image")
    if im.size[0] * im.size[1] > MAX_PIXELS:
        im.close()
        raise StationError(413, "too_large")
    return im


def picture(data: bytes) -> Picture:
    """Size, a thumbnail and the mean of a JPEG; ``bad_image`` when it is none. Only a
    reduced draft is decoded (JPEG's DCT scaling); the full frame stays as sent."""
    with open_jpeg(data) as im:
        width, height = im.size
        try:
            im.draft("RGB", (THUMB_SIDE, THUMB_SIDE))
            small = im.convert("RGB")
            small.thumbnail((THUMB_SIDE, THUMB_SIDE))
            histogram = small.convert("L").resize((64, 64)).histogram()
            out = io.BytesIO()
            small.save(out, "JPEG", quality=80)
        except Exception as exc:
            raise StationError(400, "bad_image") from exc
    mean = sum(i * n for i, n in enumerate(histogram)) / max(sum(histogram), 1)
    return Picture(width, height, out.getvalue(), mean)


def _median(histogram: list[int]) -> int:
    half, seen = sum(histogram) / 2, 0
    for value, count in enumerate(histogram):
        seen += count
        if seen >= half:
            return value
    return 0


# The track is measured in a reduced draft of at most this many pixels on its long side.
PEAK_SIDE = 2048


def track_peak(data: bytes, track: list[tuple[float, float]]) -> float:
    """The track's brightness on the 0..1 scale of SPEC §6.4 when the station sends none
    (§10.2): the brightest pixel along it above the sky's median, in a draft of the frame."""
    with open_jpeg(data) as im:
        full_w, _ = im.size
        im.draft("L", (PEAK_SIDE, PEAK_SIDE))
        gray = im.convert("L")
        width, height = gray.size
        scale = width / full_w
        sky = _median(gray.resize((64, 64)).histogram())
        best = 0
        points = [(x * scale, y * scale) for x, y in track]
        for (x1, y1), (x2, y2) in itertools.pairwise(points):
            steps = max(1, math.ceil(math.hypot(x2 - x1, y2 - y1)))
            for i in range(steps + 1):
                x = round(x1 + (x2 - x1) * i / steps)
                y = round(y1 + (y2 - y1) * i / steps)
                if 0 <= x < width and 0 <= y < height:
                    value = gray.getpixel((x, y))
                    best = max(best, int(value) if isinstance(value, int | float) else 0)
    return max(0.0, min(1.0, (best - sky) / max(255 - sky, 1)))


def cap_storage(root: Path, device_id: str, limit: int = MAX_BYTES) -> int:
    """SPEC §10.1: at most ``limit`` bytes per station; the oldest full frames go first
    (event originals and full frames, never thumbnails). Returns the files removed."""
    folder = root / device_id
    files = [p for p in folder.rglob("*.jpg") if p.is_file() and not p.is_symlink()]
    total = sum(p.stat().st_size for p in files)
    if total <= limit:
        return 0
    big = sorted(
        (p for p in files if "/full/" in p.as_posix() or "/original/" in p.as_posix()),
        key=lambda p: p.stat().st_mtime,
    )
    removed = 0
    for path in big:
        if total <= limit:
            break
        if path.name.startswith("latest-"):
            continue
        size = path.stat().st_size
        path.unlink(missing_ok=True)
        total -= size
        removed += 1
    return removed


async def remove(db: AsyncSession, device: Device) -> None:
    """The owner removes a station (SPEC §10.1): its frames, its place and its secret go at
    once (the caller deletes its images with ``ImageStore.delete_device`` after the
    commit); its measurements stay in joint results (§10.6): events and meteor pairs."""
    await db.execute(delete(Frame).where(Frame.device_id == device.id))
    await db.execute(delete(StatusSample).where(StatusSample.device_id == device.id))
    await db.execute(delete(RenderJob).where(RenderJob.device_id == device.id))
    await db.execute(delete(CameraMember).where(CameraMember.device_id == device.id))
    await db.execute(
        update(EventRecord)
        .where(EventRecord.device_id == device.id)
        .values(has_full=False, has_thumb=False, has_original=False)
    )
    device.paired_at = None
    device.latest_image_at = None
    device.latest_thumb_at = None
    forget(device)


def forget(device: Device) -> None:
    """SPEC §10.1: removing a station deletes its exact place and its secret at once (the
    images go with ``ImageStore.delete_device``); the row stays for the joint results
    (§10.6), under no account."""
    device.owner_id = None
    device.last_status = None
    device.last_frame = None
    device.station_secret_hash = None
    device.station_secret_enc = None
    device.public_slug = None
