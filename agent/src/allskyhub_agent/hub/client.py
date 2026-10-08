"""Hub client (SPEC §6): register until paired, get a token, keep a WebSocket, upload on request.

Runs in its own thread with its own asyncio loop, so the capture loop never waits for the
network (architecture rule 1). The capture loop only calls `HubClient.notify_frame()`, which
never blocks; while the hub is unreachable frames are dropped, not queued (SPEC §6.7).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import httpx
from pydantic import ValidationError
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed, InvalidStatus, WebSocketException

from allskyhub_agent.hub.backoff import Backoff
from allskyhub_agent.hub.identity import DeviceIdentity
from allskyhub_agent.hub.pairing import PairingState
from allskyhub_agent.live import LiveState
from allskyhub_agent.products.build import product_path
from allskyhub_agent.runner import FOCUS_FRAME_NAME
from allskyhub_agent.store.images import ImageStore
from allskyhub_protocol import (
    Ack,
    ChallengeRequest,
    ChallengeResponse,
    CloseCode,
    Command,
    CommandName,
    Envelope,
    ErrorCode,
    ErrorReply,
    Event,
    FrameInfo,
    FrameVariant,
    Hello,
    Products,
    Purpose,
    RegisterRequest,
    RegisterResponse,
    Status,
    TokenRequest,
    TokenResponse,
    UploadEventArgs,
    UploadFrameArgs,
    UploadProductArgs,
    parse_envelope,
)

log = logging.getLogger(__name__)

CAPABILITIES = ["upload_frame", "upload_product", "upload_event", "focus_mode"]


@dataclass(frozen=True)
class HubConfig:
    hub_url: str = "https://allskyhub.org"
    register_interval_s: float = 5.0
    status_interval_s: float = 60.0
    replaced_wait_s: float = 300.0
    http_timeout_s: float = 30.0

    @property
    def ws_url(self) -> str:
        base = self.hub_url.rstrip("/")
        if base.startswith("https://"):
            base = "wss://" + base.removeprefix("https://")
        elif base.startswith("http://"):
            base = "ws://" + base.removeprefix("http://")
        return base + "/device/v1/ws"


class WebSocketLike(Protocol):
    async def send(self, message: str) -> None: ...
    async def recv(self) -> str | bytes: ...
    def __aiter__(self) -> AsyncIterator[str | bytes]: ...


Connector = Callable[[str, str], AbstractAsyncContextManager[WebSocketLike]]


def default_connector(url: str, token: str) -> AbstractAsyncContextManager[WebSocketLike]:
    return ws_connect(  # pyright: ignore[reportReturnType]
        url, additional_headers={"Authorization": f"Bearer {token}"}, open_timeout=30
    )


class _NotPaired(Exception):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


class EventSource(Protocol):
    """Where the session gets events and their pictures (the detection worker's store)."""

    def current_events(self) -> list[Event]: ...

    def image_path(self, night: str, eid: str, thumbnail: bool) -> Path | None: ...


class HubSession:
    """The connection logic; `HubClient` runs it in a thread."""

    def __init__(
        self,
        cfg: HubConfig,
        identity: DeviceIdentity,
        pairing: PairingState,
        store: ImageStore,
        live: LiveState,
        profile: str,
        agent_version: str,
        status: Callable[[], Status | None],
        http: httpx.AsyncClient,
        connect: Connector = default_connector,
        monotonic: Callable[[], float] = time.monotonic,
        latest_products: Callable[[], Products | None] | None = None,
        events: EventSource | None = None,
    ) -> None:
        self._cfg = cfg
        self._id = identity
        self._pairing = pairing
        self._store = store
        self._live = live
        self._profile = profile
        self._version = agent_version
        self._status = status
        self._http = http
        self._connect = connect
        self._monotonic = monotonic
        self._frames: asyncio.Queue[FrameInfo] | None = None
        self._latest_products = latest_products
        self._events_src = events
        # Events wait here until the writer sends them (SPEC §6.4).
        self._events: list[Event] = []
        # Products wait here until the writer sends them; a newer night replaces an older one.
        self._products: Products | None = None
        self._uploads: set[asyncio.Task[None]] = set()
        self._backoff = Backoff()

    # --- called from the capture thread via HubClient -------------------------------------
    def offer_frame(self, info: FrameInfo) -> None:
        """Queue a frame for the open connection; dropped when offline or the queue is full."""
        q = self._frames
        if q is None or info.name == FOCUS_FRAME_NAME:
            return
        if q.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                q.get_nowait()
        q.put_nowait(info)

    def offer_products(self, products: Products) -> None:
        """Announce a night's products on the open connection (SPEC §6.3)."""
        if self._frames is not None:
            self._products = products

    def offer_event(self, event: Event) -> None:
        """Send a detection on the open connection; after a reconnect it is resent anyway."""
        if self._frames is not None:
            self._events.append(event)

    # --- main loop ---------------------------------------------------------------------
    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            delay = 0.0
            try:
                await self._until_paired(stop)
                if stop.is_set():
                    return
                token = await self._token()
                code = await self._session(token, stop)
                if code == CloseCode.REPLACED:
                    log.warning("another connection of this device took over; waiting")
                    delay = self._cfg.replaced_wait_s
                elif code == CloseCode.UNPAIRED:
                    log.info("device was removed from its account; pairing again")
                    self._pairing.set_unpaired(None, None, self._monotonic())
                elif code != CloseCode.REAUTH:
                    delay = self._backoff.next()
            except _NotPaired:
                self._pairing.set_unpaired(None, None, self._monotonic())
            except (httpx.HTTPError, OSError, WebSocketException, ValidationError) as exc:
                log.info("hub not reachable: %s", exc)
                delay = self._backoff.next()
            if delay > 0:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), delay)

    async def _nonce(self) -> str:
        r = await self._http.post(
            "/device/v1/challenge",
            json=ChallengeRequest(device_id=self._id.device_id).model_dump(),
        )
        r.raise_for_status()
        return ChallengeResponse.model_validate_json(r.content).nonce

    async def _until_paired(self, stop: asyncio.Event) -> None:
        """SPEC §6.2 steps 2-4: register every few seconds until the hub says paired."""
        while not stop.is_set():
            nonce = await self._nonce()
            body = RegisterRequest(
                public_key=self._id.public_key_b64,
                profile=self._profile,
                agent_version=self._version,
                nonce=nonce,
                signature=self._id.sign(Purpose.REGISTER, nonce),
            )
            r = await self._http.post("/device/v1/register", json=body.model_dump())
            r.raise_for_status()
            reg = RegisterResponse.model_validate_json(r.content)
            if reg.paired:
                self._pairing.set_paired()
                return
            self._pairing.set_unpaired(reg.pairing_code, reg.expires_in, self._monotonic())
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), self._cfg.register_interval_s)

    async def _token(self) -> str:
        nonce = await self._nonce()
        body = TokenRequest(
            device_id=self._id.device_id,
            nonce=nonce,
            signature=self._id.sign(Purpose.TOKEN, nonce),
        )
        r = await self._http.post("/device/v1/token", json=body.model_dump())
        if r.status_code == 403:
            raise _NotPaired
        r.raise_for_status()
        return TokenResponse.model_validate_json(r.content).access_token

    async def _session(self, token: str, stop: asyncio.Event) -> int | None:
        """One WebSocket connection; returns the close code (None if we stopped)."""
        try:
            async with self._connect(self._cfg.ws_url, token) as ws:
                self._frames = asyncio.Queue(maxsize=10)
                self._pairing.set_connected(True)
                self._backoff.reset()
                await self._send(
                    ws,
                    Hello(
                        device_id=self._id.device_id,
                        profile=self._profile,
                        agent_version=self._version,
                        capabilities=CAPABILITIES,
                    ),
                )
                if self._latest_products is not None:
                    # SPEC §6.7: the newest night's products after every (re)connect.
                    self._products = await asyncio.to_thread(self._latest_products)
                if self._events_src is not None:
                    # SPEC §6.4/§6.7: the current night's events again; the hub upserts.
                    self._events = await asyncio.to_thread(self._events_src.current_events)
                tasks: list[asyncio.Task[Any]] = [
                    asyncio.create_task(self._reader(ws, token)),
                    asyncio.create_task(self._writer(ws)),
                    asyncio.create_task(stop.wait()),
                ]
                try:
                    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for t in tasks:
                        t.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                for t in done:
                    exc = t.exception()
                    if isinstance(exc, ConnectionClosed):
                        return exc.rcvd.code if exc.rcvd else None
                    if exc is not None:
                        raise exc
                return None
        except InvalidStatus as exc:
            if exc.response.status_code == 401:
                return CloseCode.REAUTH
            raise
        except ConnectionClosed as exc:
            return exc.rcvd.code if exc.rcvd else None
        finally:
            self._frames = None
            self._products = None
            self._events = []
            for task in self._uploads:
                task.cancel()
            self._pairing.set_connected(False)

    async def _send(
        self,
        ws: WebSocketLike,
        body: Hello | Status | FrameInfo | Products | Event | Ack | ErrorReply,
    ) -> None:
        await ws.send(Envelope.wrap(body, ts=_now()).model_dump_json())

    async def _writer(self, ws: WebSocketLike) -> None:
        q = self._frames
        if q is None:
            return
        next_status = 0.0
        while True:
            now = self._monotonic()
            products, self._products = self._products, None
            if products is not None:
                await self._send(ws, products)
            events, self._events = self._events, []
            for event in events:
                await self._send(ws, event)
            if now >= next_status:
                status = self._status()
                if status is not None:
                    await self._send(ws, status)
                next_status = now + self._cfg.status_interval_s
            try:
                frame = await asyncio.wait_for(q.get(), min(0.5, max(0.1, next_status - now)))
            except TimeoutError:
                continue
            await self._send(ws, frame)

    async def _reader(self, ws: WebSocketLike, token: str) -> None:
        async for raw in ws:
            try:
                env = parse_envelope(raw)
            except ValidationError:
                log.warning("invalid message from hub")
                continue
            if not isinstance(env.body, Command):
                continue
            if env.body.name is CommandName.UPLOAD_PRODUCT:
                # A timelapse can take minutes to upload; keep reading commands meanwhile.
                task = asyncio.create_task(self._reply_later(ws, env.id, env.body, token))
                self._uploads.add(task)
                task.add_done_callback(self._uploads.discard)
                continue
            reply = await self._command(env.id, env.body, token)
            await self._send(ws, reply)

    async def _reply_later(self, ws: WebSocketLike, ref: str, cmd: Command, token: str) -> None:
        reply = await self._upload_product(ref, cmd, token)
        with contextlib.suppress(ConnectionClosed):
            await self._send(ws, reply)

    async def _command(self, ref: str, cmd: Command, token: str) -> Ack | ErrorReply:
        if cmd.name is CommandName.UPLOAD_FRAME:
            return await self._upload(ref, cmd, token)
        if cmd.name is CommandName.UPLOAD_EVENT:
            return await self._upload_event(ref, cmd, token)
        if cmd.name is CommandName.FOCUS_MODE:
            on = cmd.args.get("on")
            if not isinstance(on, bool):
                return ErrorReply(ref=ref, code=ErrorCode.INVALID_ARGS, message="need {on: bool}")
            self._live.set_focus_mode(on)
            return Ack(ref=ref)
        return ErrorReply(ref=ref, code=ErrorCode.UNSUPPORTED, message=cmd.name.value)

    async def _upload(self, ref: str, cmd: Command, token: str) -> Ack | ErrorReply:
        """SPEC §6.5: PUT the requested image, then ack."""
        try:
            args = UploadFrameArgs.model_validate(cmd.args)
        except ValidationError:
            return ErrorReply(ref=ref, code=ErrorCode.INVALID_ARGS)
        path = self._store.path_for(
            args.night_id, args.name, thumbnail=args.variant is FrameVariant.THUMB
        )
        if path is None:
            return ErrorReply(ref=ref, code=ErrorCode.NOT_FOUND)
        data = await asyncio.to_thread(path.read_bytes)
        try:
            r = await self._http.put(
                f"/device/v1/frames/{args.night_id}/{args.name}",
                params={"variant": args.variant.value},
                content=data,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "image/jpeg"},
            )
        except httpx.HTTPError as exc:
            return ErrorReply(ref=ref, code=ErrorCode.FAILED, message=str(exc)[:200])
        if r.is_success:
            return Ack(ref=ref)
        return ErrorReply(ref=ref, code=ErrorCode.FAILED, message=f"HTTP {r.status_code}")

    async def _upload_event(self, ref: str, cmd: Command, token: str) -> Ack | ErrorReply:
        """SPEC §6.5: PUT the event's picture, then ack."""
        try:
            args = UploadEventArgs.model_validate(cmd.args)
        except ValidationError:
            return ErrorReply(ref=ref, code=ErrorCode.INVALID_ARGS)
        path = (
            self._events_src.image_path(
                args.night_id, args.event_id, thumbnail=args.variant is FrameVariant.THUMB
            )
            if self._events_src is not None
            else None
        )
        if path is None:
            return ErrorReply(ref=ref, code=ErrorCode.NOT_FOUND)
        data = await asyncio.to_thread(path.read_bytes)
        try:
            r = await self._http.put(
                f"/device/v1/events/{args.night_id}/{args.event_id}",
                params={"variant": args.variant.value},
                content=data,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "image/jpeg"},
            )
        except httpx.HTTPError as exc:
            return ErrorReply(ref=ref, code=ErrorCode.FAILED, message=str(exc)[:200])
        if r.is_success:
            return Ack(ref=ref)
        return ErrorReply(ref=ref, code=ErrorCode.FAILED, message=f"HTTP {r.status_code}")

    async def _upload_product(self, ref: str, cmd: Command, token: str) -> Ack | ErrorReply:
        """SPEC §6.5: PUT the requested night product, streamed, then ack."""
        try:
            args = UploadProductArgs.model_validate(cmd.args)
        except ValidationError:
            return ErrorReply(ref=ref, code=ErrorCode.INVALID_ARGS)
        thumb = args.variant is FrameVariant.THUMB
        path = product_path(self._store, args.night_id, args.name, thumbnail=thumb)
        if path is None:
            return ErrorReply(ref=ref, code=ErrorCode.NOT_FOUND)
        ctype = "video/mp4" if path.suffix == ".mp4" else "image/jpeg"
        size = path.stat().st_size

        async def chunks() -> AsyncIterator[bytes]:
            with path.open("rb") as f:
                while chunk := await asyncio.to_thread(f.read, 1 << 20):
                    yield chunk

        try:
            r = await self._http.put(
                f"/device/v1/products/{args.night_id}/{args.name}",
                params={"variant": args.variant.value},
                content=chunks(),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": ctype,
                    "Content-Length": str(size),
                },
                timeout=httpx.Timeout(self._cfg.http_timeout_s, write=None),
            )
        except (httpx.HTTPError, OSError) as exc:
            return ErrorReply(ref=ref, code=ErrorCode.FAILED, message=str(exc)[:200])
        if r.is_success:
            return Ack(ref=ref)
        return ErrorReply(ref=ref, code=ErrorCode.FAILED, message=f"HTTP {r.status_code}")


class HubClient:
    """Runs a `HubSession` in a daemon thread."""

    def __init__(
        self, make_session: Callable[[httpx.AsyncClient], HubSession], cfg: HubConfig
    ) -> None:
        self._make = make_session
        self._cfg = cfg
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._session: HubSession | None = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._main, name="hub", daemon=True)

    def start(self) -> None:
        self._thread.start()
        self._ready.wait(10)

    def _main(self) -> None:
        asyncio.run(self._amain())

    async def _amain(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        async with httpx.AsyncClient(
            base_url=self._cfg.hub_url, timeout=self._cfg.http_timeout_s
        ) as http:
            self._session = self._make(http)
            self._ready.set()
            await self._session.run(self._stop)

    def notify_frame(self, info: FrameInfo) -> None:
        """Never blocks; safe to call from the capture thread."""
        loop, session = self._loop, self._session
        if loop is not None and session is not None and not loop.is_closed():
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(session.offer_frame, info)

    def notify_products(self, products: Products) -> None:
        """Never blocks; safe to call from the products worker."""
        loop, session = self._loop, self._session
        if loop is not None and session is not None and not loop.is_closed():
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(session.offer_products, products)

    def notify_event(self, event: Event) -> None:
        """Never blocks; safe to call from the detection worker."""
        loop, session = self._loop, self._session
        if loop is not None and session is not None and not loop.is_closed():
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(session.offer_event, event)

    def stop(self, timeout: float = 5.0) -> None:
        loop, stop = self._loop, self._stop
        if loop is not None and stop is not None and not loop.is_closed():
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(stop.set)
        self._thread.join(timeout)
