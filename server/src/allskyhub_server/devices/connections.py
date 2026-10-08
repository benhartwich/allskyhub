"""Open device WebSockets, requested uploads and live viewers, in this process.

The hub runs as one process for now (``allskyhub-server serve --workers 1``): the device's
WebSocket and its uploads must reach the same process. Spreading over several workers
needs a shared registry (for example PostgreSQL LISTEN/NOTIFY) and is not done yet.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

from allskyhub_protocol import (
    Ack,
    Command,
    CommandName,
    Envelope,
    ErrorReply,
    FrameVariant,
    UploadEventArgs,
    UploadFrameArgs,
    UploadProductArgs,
)

log = logging.getLogger(__name__)

# SPEC §6.5: how long after asking the hub accepts the start of an upload. Products wait
# longer: the device may still be busy with an earlier timelapse.
UPLOAD_WINDOW = dt.timedelta(minutes=2)
PRODUCT_UPLOAD_WINDOW = dt.timedelta(minutes=30)
# A viewer counts as watching for this long after the page last fetched an image.
LIVE_HOLD = dt.timedelta(seconds=30)

SendText = Callable[[str], Awaitable[None]]
Close = Callable[[int], Awaitable[None]]
# (what, night_id, name, variant); what is "frame", "product" or "event".
UploadKey = tuple[str, str, str, FrameVariant]
Kind = Literal["frame", "product", "event"]


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


@dataclass
class Connection:
    device_id: str
    send_text: SendText
    close: Close
    # (night_id, name, variant) → until when the hub accepts that upload.
    requested: dict[UploadKey, dt.datetime] = field(default_factory=dict[UploadKey, dt.datetime])
    # Commands waiting for their ack/error (SPEC §6.5), by envelope id.
    replies: dict[str, asyncio.Future[Ack | ErrorReply]] = field(
        default_factory=dict[str, "asyncio.Future[Ack | ErrorReply]"]
    )


class DeviceOfflineError(Exception):
    """The device has no open WebSocket."""


class ConnectionRegistry:
    def __init__(self) -> None:
        self._conns: dict[str, Connection] = {}
        self._live_until: dict[str, dt.datetime] = {}
        self._lock = asyncio.Lock()

    async def attach(self, conn: Connection) -> Connection | None:
        """Register ``conn``; returns the connection it replaces (SPEC §6.1, close 4409)."""
        async with self._lock:
            old = self._conns.get(conn.device_id)
            self._conns[conn.device_id] = conn
        return old

    async def detach(self, conn: Connection) -> None:
        async with self._lock:
            if self._conns.get(conn.device_id) is conn:
                del self._conns[conn.device_id]

    def get(self, device_id: str) -> Connection | None:
        return self._conns.get(device_id)

    def is_online(self, device_id: str) -> bool:
        return device_id in self._conns

    async def close(self, device_id: str, code: int) -> None:
        conn = self._conns.get(device_id)
        if conn is not None:
            await conn.close(code)

    # --- live view --------------------------------------------------------------------

    def touch_live(self, device_id: str) -> None:
        self._live_until[device_id] = _now() + LIVE_HOLD

    def is_live(self, device_id: str) -> bool:
        until = self._live_until.get(device_id)
        return until is not None and until > _now()

    # --- commands with a reply (SPEC §6.5) -------------------------------------------------

    async def command(self, device_id: str, cmd: Command, wait_s: float) -> Ack | ErrorReply:
        """Send ``cmd`` and wait for its ``ack`` or ``error``.

        Raises ``DeviceOffline`` when the device is not connected and ``TimeoutError`` when
        it does not answer in time."""
        conn = self._conns.get(device_id)
        if conn is None:
            raise DeviceOfflineError(device_id)
        env = Envelope.wrap(cmd, ts=_now())
        reply: asyncio.Future[Ack | ErrorReply] = asyncio.get_running_loop().create_future()
        conn.replies[env.id] = reply
        try:
            await conn.send_text(env.model_dump_json())
            return await asyncio.wait_for(reply, wait_s)
        finally:
            conn.replies.pop(env.id, None)

    def resolve(self, device_id: str, reply: Ack | ErrorReply) -> None:
        """An ``ack``/``error`` arrived; wake whoever waits for it (others are ignored)."""
        conn = self._conns.get(device_id)
        future = conn.replies.get(reply.ref) if conn else None
        if future is not None and not future.done():
            future.set_result(reply)

    # --- uploads (SPEC §6.5) ----------------------------------------------------------

    async def request_upload(
        self, device_id: str, night_id: str, name: str, variant: FrameVariant
    ) -> bool:
        """Send ``upload_frame``; False when the device is not connected."""
        args = UploadFrameArgs(night_id=night_id, name=name, variant=variant)
        cmd = Command(name=CommandName.UPLOAD_FRAME, args=args.model_dump(mode="json"))
        return await self._request(
            device_id, ("frame", night_id, name, variant), cmd, UPLOAD_WINDOW
        )

    async def request_product(
        self, device_id: str, night_id: str, name: str, variant: FrameVariant
    ) -> bool:
        """Send ``upload_product``; False when the device is not connected."""
        args = UploadProductArgs.model_validate(
            {"night_id": night_id, "name": name, "variant": variant}
        )
        cmd = Command(name=CommandName.UPLOAD_PRODUCT, args=args.model_dump(mode="json"))
        key = ("product", night_id, name, variant)
        return await self._request(device_id, key, cmd, PRODUCT_UPLOAD_WINDOW)

    async def request_event(
        self, device_id: str, night_id: str, event_id: str, variant: FrameVariant
    ) -> bool:
        """Send ``upload_event``; False when the device is not connected."""
        args = UploadEventArgs(night_id=night_id, event_id=event_id, variant=variant)
        cmd = Command(name=CommandName.UPLOAD_EVENT, args=args.model_dump(mode="json"))
        key = ("event", night_id, event_id, variant)
        return await self._request(device_id, key, cmd, UPLOAD_WINDOW)

    async def _request(
        self, device_id: str, key: UploadKey, cmd: Command, window: dt.timedelta
    ) -> bool:
        conn = self._conns.get(device_id)
        if conn is None:
            return False
        now = _now()
        conn.requested = {k: v for k, v in conn.requested.items() if v > now}
        conn.requested[key] = now + window
        await conn.send_text(Envelope.wrap(cmd, ts=now).model_dump_json())
        return True

    def is_requested(
        self, device_id: str, night_id: str, name: str, variant: FrameVariant, what: Kind = "frame"
    ) -> bool:
        """The hub asked for this upload and the window to start it is still open."""
        conn = self._conns.get(device_id)
        until = conn.requested.get((what, night_id, name, variant)) if conn else None
        return until is not None and until > _now()

    def is_pending(
        self, device_id: str, night_id: str, name: str, variant: FrameVariant, what: Kind
    ) -> bool:
        """Asked for and not yet delivered (the app shows "wird geholt")."""
        return self.is_requested(device_id, night_id, name, variant, what)

    def take_upload(
        self, device_id: str, night_id: str, name: str, variant: FrameVariant, what: Kind = "frame"
    ) -> bool:
        """Mark a valid upload as done. The window was checked when the upload started, so a
        long one that ends after it still counts."""
        conn = self._conns.get(device_id)
        if conn is None:
            return False
        return conn.requested.pop((what, night_id, name, variant), None) is not None
