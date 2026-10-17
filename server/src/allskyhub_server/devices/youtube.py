"""The night's timelapse on the owner's YouTube channel (roadmap H5).

The owner connects their channel on the hub with Google's OAuth (scope ``youtube.upload``
only), never on the camera. The hub keeps the refresh token in ``device.youtube``; the
OAuth client is the admin's (admin/stores.py, kind ``youtube``). Each morning, once the
camera has sent last night's timelapse, the hub uploads it, once; the day timelapse at
dusk if the owner wants it. Nights older than ``RECENT`` are never uploaded, so an import
or a camera that was offline for a week does not fill the channel with old nights.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import secrets
import urllib.parse
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

import httpx
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_protocol import FrameVariant, ProductKind
from allskyhub_server.admin.stores import Vault
from allskyhub_server.devices import night_summary
from allskyhub_server.devices.connections import ConnectionRegistry
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.models import Device, Product

log = logging.getLogger(__name__)

AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"  # noqa: S105 - the address
REVOKE = "https://oauth2.googleapis.com/revoke"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"
SCOPE = "https://www.googleapis.com/auth/youtube.upload"
CALLBACK = "/youtube/callback"
STATE_COOKIE = "ash_youtube"
STATE_AGE_S = 600
CATEGORY_SCIENCE = "28"  # YouTube's "Science & Technology"
RECENT = dt.timedelta(days=2)
CHECK_EVERY_S = 300
TRIES = 3
TIMEOUT = httpx.Timeout(30.0, write=600.0)
CHUNK = 1024 * 1024
PRIVACY = {
    "unlisted": "Nicht gelistet (nur mit Link)",
    "private": "Privat",
    "public": "Öffentlich",
}
Privacy = Literal["unlisted", "private", "public"]
KINDS = {
    ProductKind.TIMELAPSE.value: "timelapse.mp4",
    ProductKind.DAY_TIMELAPSE.value: "day-timelapse.mp4",
}
MONTHS = (
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
)  # fmt: skip


class YouTubeSettings(BaseModel):
    """``device.youtube``; ``broken`` once Google refused the refresh token (revoked in the
    Google account, or the OAuth client was replaced)."""

    refresh_token: str
    privacy: Privacy = "unlisted"
    day: bool = False
    channel: str | None = None
    broken: bool = False
    since: str  # the first night id that may be uploaded: none from before connecting


def settings_of(device: Device) -> YouTubeSettings | None:
    try:
        return YouTubeSettings.model_validate(device.youtube) if device.youtube else None
    except ValidationError:
        return None


def wanted_kinds(device: Device) -> frozenset[str]:
    """The product kinds the hub fetches in full at once for YouTube."""
    config = settings_of(device)
    if config is None or config.broken:
        return frozenset()
    if config.day:
        return frozenset({ProductKind.TIMELAPSE.value, ProductKind.DAY_TIMELAPSE.value})
    return frozenset({ProductKind.TIMELAPSE.value})


def redirect_uri(base_url: str) -> str:
    return base_url.rstrip("/") + CALLBACK


@dataclass(frozen=True)
class Client:
    client_id: str
    client_secret: str


def client_of(vault: Vault) -> Client | None:
    """The admin's OAuth client, None until it is uploaded."""
    if not vault.has("youtube"):
        return None
    data = json.loads(vault.secret("youtube", "YouTube OAuth"))
    web = cast(dict[str, Any], data["web"])
    return Client(str(web["client_id"]), str(web["client_secret"]))


def new_state(device_id: str) -> tuple[str, str]:
    """(state for Google, cookie value): the callback compares both, so only the browser
    that started the connection can finish it."""
    state = secrets.token_urlsafe(24)
    return state, f"{device_id}.{state}"


def check_state(cookie: str | None, state: str) -> str | None:
    """The device id the connection was started for, None if the state does not match."""
    if not cookie or "." not in cookie:
        return None
    device_id, expected = cookie.rsplit(".", 1)
    return device_id if secrets.compare_digest(expected, state) else None


def auth_url(client: Client, base_url: str, state: str) -> str:
    query = {
        "client_id": client.client_id,
        "redirect_uri": redirect_uri(base_url),
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",  # else Google sends no refresh token on a second connection
        "state": state,
    }
    return f"{AUTH}?{urllib.parse.urlencode(query)}"


class YouTubeError(Exception):
    """German message for the owner; never contains a token."""


def _title_date(night_id: str) -> dt.date:
    return dt.date(int(night_id[:4]), int(night_id[4:6]), int(night_id[6:8]))


def _day(d: dt.date) -> str:
    return f"{d.day}. {MONTHS[d.month - 1]} {d.year}"


def title(device: Device, night_id: str, kind: str) -> str:
    evening = _title_date(night_id)
    if kind == ProductKind.DAY_TIMELAPSE.value:
        return f"{device.name}: der Tag am {_day(evening)}"[:100]
    morning = evening + dt.timedelta(days=1)
    if evening.month == morning.month:
        span = f"{evening.day}./{_day(morning)}"
    else:
        span = f"{evening.day}. {MONTHS[evening.month - 1]}/{_day(morning)}"
    return f"{device.name}: die Nacht {span}"[:100]


class YouTube:
    def __init__(
        self,
        maker: async_sessionmaker[AsyncSession],
        images: ImageStore,
        registry: ConnectionRegistry,
        vault: Vault,
        base_url: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._maker = maker
        self._images = images
        self._registry = registry
        self._vault = vault
        self._base = base_url.rstrip("/")
        self._http = client
        self._tries: dict[tuple[str, str, str], int] = {}

    @property
    def available(self) -> bool:
        return self._vault.has("youtube")

    def _client(self) -> Client:
        client = client_of(self._vault)
        if client is None:
            raise YouTubeError("YouTube ist auf diesem Hub noch nicht eingerichtet.")
        return client

    def _httpx(self) -> httpx.AsyncClient:
        return self._http or httpx.AsyncClient(timeout=TIMEOUT)

    async def _close(self, http: httpx.AsyncClient) -> None:
        if self._http is None:
            await http.aclose()

    def start(self, device_id: str) -> tuple[str, str]:
        """(Google's consent page, cookie value)."""
        state, cookie = new_state(device_id)
        return auth_url(self._client(), self._base, state), cookie

    async def connect(self, code: str) -> str:
        """The refresh token for the code from Google's redirect."""
        client = self._client()
        http = self._httpx()
        try:
            r = await http.post(
                TOKEN,
                data={
                    "code": code,
                    "client_id": client.client_id,
                    "client_secret": client.client_secret,
                    "redirect_uri": redirect_uri(self._base),
                    "grant_type": "authorization_code",
                },
            )
        except httpx.HTTPError:
            raise YouTubeError(
                "Google war nicht erreichbar. Bitte noch einmal versuchen."
            ) from None
        finally:
            await self._close(http)
        body = _json(r)
        token = body.get("refresh_token")
        if not r.is_success or not isinstance(token, str):
            log.info("youtube: code exchange failed", extra={"status": r.status_code})
            raise YouTubeError(
                "Google hat die Verbindung nicht bestätigt. Bitte noch einmal versuchen."
            )
        if SCOPE not in str(body.get("scope", "")).split():
            raise YouTubeError(
                "Ohne die Erlaubnis zum Hochladen geht es nicht. Bitte beim Verbinden das "
                "Häkchen bei YouTube setzen."
            )
        return token

    async def revoke(self, refresh_token: str) -> None:
        http = self._httpx()
        try:
            await http.post(REVOKE, data={"token": refresh_token})
        except httpx.HTTPError:
            log.info("youtube: revoke failed")
        finally:
            await self._close(http)

    async def _access_token(self, http: httpx.AsyncClient, config: YouTubeSettings) -> str | None:
        """None when Google refuses the refresh token for good; raises on a passing error."""
        client = self._client()
        r = await http.post(
            TOKEN,
            data={
                "refresh_token": config.refresh_token,
                "client_id": client.client_id,
                "client_secret": client.client_secret,
                "grant_type": "refresh_token",
            },
        )
        body = _json(r)
        if r.status_code in (400, 401) and body.get("error") in (
            "invalid_grant",
            "invalid_client",
            "unauthorized_client",
        ):
            return None
        token = body.get("access_token")
        if not r.is_success or not isinstance(token, str):
            raise YouTubeError(f"token {r.status_code}")
        return token

    def _description(self, device: Device, night_id: str, kind: str, line: str) -> str:
        parts = [
            "Zeitraffer des Tages"
            if kind == ProductKind.DAY_TIMELAPSE.value
            else "Zeitraffer der Nacht",
            f"Allsky-Kamera {device.name}, aufgenommen mit allskyhub.",
        ]
        if line:
            parts.append(line)
        if device.public_slug:
            parts.append(f"{self._base}/sky/{device.public_slug}/nights/{night_id}")
        parts.append("https://allskyhub.org")
        return "\n\n".join(parts)[:5000]

    async def upload(
        self, http: httpx.AsyncClient, token: str, path: Path, meta: dict[str, Any]
    ) -> dict[str, Any]:
        """Resumable upload in one go: open the session, then stream the file."""
        size = (await asyncio.to_thread(path.stat)).st_size
        r = await http.post(
            UPLOAD,
            params={"uploadType": "resumable", "part": "snippet,status"},
            headers={
                "Authorization": f"Bearer {token}",
                "X-Upload-Content-Type": "video/mp4",
                "X-Upload-Content-Length": str(size),
            },
            json=meta,
        )
        location = r.headers.get("location")
        if not r.is_success or not location:
            raise YouTubeError(f"session {r.status_code} {_reason(r)}")

        async def chunks() -> AsyncIterator[bytes]:
            with path.open("rb") as fh:
                while block := await asyncio.to_thread(fh.read, CHUNK):
                    yield block

        r = await http.put(
            location,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "video/mp4",
                "Content-Length": str(size),
            },
            content=chunks(),
        )
        if not r.is_success:
            raise YouTubeError(f"upload {r.status_code} {_reason(r)}")
        return _json(r)

    async def check(self, now: dt.datetime | None = None) -> None:
        """One pass: every connected camera's recent timelapses that are not on YouTube."""
        if not self.available:
            return
        now = now or dt.datetime.now(dt.UTC)
        oldest = (now - RECENT).strftime("%Y%m%d")
        async with self._maker() as db:
            devices = list(
                await db.scalars(
                    select(Device).where(Device.youtube.is_not(None), Device.owner_id.is_not(None))
                )
            )
            for device in devices:
                config = settings_of(device)
                if config is None or config.broken:
                    continue
                kinds = [ProductKind.TIMELAPSE.value]
                if config.day:
                    kinds.append(ProductKind.DAY_TIMELAPSE.value)
                products = list(
                    await db.scalars(
                        select(Product)
                        .where(
                            Product.device_id == device.id,
                            Product.kind.in_(kinds),
                            Product.youtube_id.is_(None),
                            Product.night_id >= max(oldest, config.since),
                        )
                        .order_by(Product.night_id)
                    )
                )
                for product in products:
                    await self._one(db, device, config, product)

    async def _one(
        self, db: AsyncSession, device: Device, config: YouTubeSettings, product: Product
    ) -> None:
        key = (device.id, product.night_id, product.name)
        if self._tries.get(key, 0) >= TRIES:
            return
        path = self._images.product_path(
            device.id, product.night_id, product.name, FrameVariant.FULL
        )
        if not product.has_full or not path.is_file():
            if not self._registry.is_pending(
                device.id, product.night_id, product.name, FrameVariant.FULL, "product"
            ):
                await self._registry.request_product(
                    device.id, product.night_id, product.name, FrameVariant.FULL
                )
            return
        summary = ""
        if product.kind == ProductKind.TIMELAPSE.value:
            found = await night_summary.summarize(db, device.id, product.night_id)
            summary = "" if found.empty else found.line()
        meta = {
            "snippet": {
                "title": title(device, product.night_id, product.kind),
                "description": self._description(device, product.night_id, product.kind, summary),
                "tags": ["allsky", "Allsky-Kamera", "Zeitraffer", "Nachthimmel", "allskyhub"],
                "categoryId": CATEGORY_SCIENCE,
            },
            "status": {"privacyStatus": config.privacy, "selfDeclaredMadeForKids": False},
        }
        http = self._httpx()
        try:
            token = await self._access_token(http, config)
            if token is None:
                device.youtube = {**config.model_dump(), "broken": True}
                await db.commit()
                log.info("youtube: refresh token refused", extra={"device": device.id})
                return
            video = await self.upload(http, token, path, meta)
        except (httpx.HTTPError, YouTubeError) as exc:
            self._tries[key] = self._tries.get(key, 0) + 1
            log.warning(
                "youtube: upload failed",
                extra={
                    "device": device.id,
                    "night": product.night_id,
                    "error": str(exc) or type(exc).__name__,
                },
            )
            return
        finally:
            await self._close(http)
        video_id = video.get("id")
        if not isinstance(video_id, str):
            self._tries[key] = TRIES
            return
        product.youtube_id = video_id[:32]
        channel = cast(dict[str, Any], video.get("snippet") or {}).get("channelTitle")
        if isinstance(channel, str) and channel != config.channel:
            device.youtube = {**config.model_dump(), "channel": channel[:100]}
        await db.commit()
        log.info(
            "youtube: uploaded",
            extra={"device": device.id, "night": product.night_id, "video": video_id},
        )

    async def run_forever(self) -> None:
        while True:
            try:
                await self.check()
            except Exception:
                log.exception("youtube: check failed")
            await asyncio.sleep(CHECK_EVERY_S)


def video_url(video_id: str) -> str:
    return f"https://youtu.be/{video_id}"


def _json(r: httpx.Response) -> dict[str, Any]:
    try:
        body: object = r.json()
    except ValueError:
        return {}
    return cast(dict[str, Any], body) if isinstance(body, dict) else {}


def _reason(r: httpx.Response) -> str:
    """Google's error reason (``quotaExceeded``, ``uploadLimitExceeded``), never a token."""
    error = _json(r).get("error")
    if isinstance(error, dict):
        errors = cast(dict[str, Any], error).get("errors")
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            return str(cast(dict[str, Any], errors[0]).get("reason", ""))[:60]
    return ""
