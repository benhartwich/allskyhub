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

from allskyhub_protocol import Command, CommandName, Envelope, FrameVariant, UploadFrameArgs

log = logging.getLogger(__name__)

# SPEC §6.5: how long the hub accepts an upload it asked for.
UPLOAD_WINDOW = dt.timedelta(minutes=2)
# A viewer counts as watching for this long after the page last fetched an image.
LIVE_HOLD = dt.timedelta(seconds=30)

SendText = Callable[[str], Awaitable[None]]
Close = Callable[[int], Awaitable[None]]
UploadKey = tuple[str, str, FrameVariant]


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


@dataclass
class Connection:
    device_id: str
    send_text: SendText
    close: Close
    # (night_id, name, variant) → until when the hub accepts that upload.
    requested: dict[UploadKey, dt.datetime] = field(default_factory=dict[UploadKey, dt.datetime])


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

    # --- uploads (SPEC §6.5) ----------------------------------------------------------

    async def request_upload(
        self, device_id: str, night_id: str, name: str, variant: FrameVariant
    ) -> bool:
        """Send ``upload_frame``; False when the device is not connected."""
        conn = self._conns.get(device_id)
        if conn is None:
            return False
        args = UploadFrameArgs(night_id=night_id, name=name, variant=variant)
        now = _now()
        conn.requested = {k: v for k, v in conn.requested.items() if v > now}
        conn.requested[(night_id, name, variant)] = now + UPLOAD_WINDOW
        cmd = Command(name=CommandName.UPLOAD_FRAME, args=args.model_dump(mode="json"))
        await conn.send_text(Envelope.wrap(cmd, ts=now).model_dump_json())
        return True

    def is_requested(self, device_id: str, night_id: str, name: str, variant: FrameVariant) -> bool:
        """The hub asked for this upload and the window is still open."""
        conn = self._conns.get(device_id)
        until = conn.requested.get((night_id, name, variant)) if conn else None
        return until is not None and until > _now()

    def take_upload(self, device_id: str, night_id: str, name: str, variant: FrameVariant) -> bool:
        """Accept an upload only if the hub asked for it and the window is open."""
        conn = self._conns.get(device_id)
        if conn is None:
            return False
        until = conn.requested.pop((night_id, name, variant), None)
        return until is not None and until > _now()
