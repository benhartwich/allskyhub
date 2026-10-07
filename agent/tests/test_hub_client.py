"""Hub client against a fake hub (SPEC §6): real Ed25519, real WebSocket, mocked HTTP."""

from __future__ import annotations

import asyncio
import json
import random
import stat
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import numpy as np
import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from allskyhub_agent.hub.backoff import MAX_S, Backoff
from allskyhub_agent.hub.client import HubConfig, HubSession
from allskyhub_agent.hub.identity import DeviceIdentity
from allskyhub_agent.hub.pairing import PairingState
from allskyhub_agent.live import LiveState
from allskyhub_agent.products.build import newest_products
from allskyhub_agent.store.images import ImageStore
from allskyhub_agent.web.server import WebServer
from allskyhub_protocol import (
    Ack,
    Command,
    CommandName,
    Envelope,
    ErrorReply,
    FrameInfo,
    Hello,
    Mode,
    Products,
    Purpose,
    Status,
    b64url_decode,
    device_id_from_public_key,
    parse_envelope,
    signing_payload,
)

TZ = ZoneInfo("Europe/Vienna")
TS = datetime(2026, 10, 6, 20, 0, 5, tzinfo=UTC)


def test_identity_is_created_once_with_private_mode(tmp_path: Path) -> None:
    key = tmp_path / "sub" / "device.key"
    a = DeviceIdentity.load_or_create(key)
    assert stat.S_IMODE(key.stat().st_mode) == 0o600
    b = DeviceIdentity.load_or_create(key)
    assert a.device_id == b.device_id
    assert len(a.device_id) == 26


def test_backoff_doubles_with_jitter_and_caps() -> None:
    b = Backoff(random.Random(1))
    delays = [b.next() for _ in range(15)]
    assert 0.5 <= delays[0] <= 1.0
    assert 1.0 <= delays[1] <= 2.0
    assert all(d <= MAX_S for d in delays)
    assert delays[-1] >= MAX_S / 2
    b.reset()
    assert b.next() <= 1.0


def test_pairing_code_expires_locally() -> None:
    p = PairingState("a" * 26, "https://hub", "sim", "0.1.0")
    p.set_unpaired("ABCDEF", 60, now=100.0)
    assert p.info(now=130.0).pairing_code == "ABCDEF"
    assert p.info(now=130.0).expires_in == 30
    assert p.info(now=161.0).pairing_code is None
    p.set_paired()
    assert p.info(now=0.0).paired is True


@dataclass
class FakeHub:
    """HTTP side as an httpx MockTransport, WebSocket side as a real local server."""

    register_unpaired: int = 2
    close_code: int = 4403
    public_key: bytes | None = None
    registers: int = 0
    tokens: int = 0
    nonces: set[str] = field(default_factory=set[str])
    uploads: list[tuple[str, str, bytes]] = field(default_factory=list[tuple[str, str, bytes]])
    upload_headers: list[dict[str, str]] = field(default_factory=list[dict[str, str]])
    received: list[Envelope] = field(default_factory=list[Envelope])
    ws_auth: list[str] = field(default_factory=list[str])
    commands: list[Command] = field(default_factory=list[Command])
    done: asyncio.Event = field(default_factory=asyncio.Event)
    replies: dict[str, Ack | ErrorReply] = field(default_factory=dict[str, Ack | ErrorReply])
    reply_order: list[Ack | ErrorReply | None] = field(
        default_factory=list[Ack | ErrorReply | None]
    )

    def _verify(self, purpose: Purpose, device_id: str, nonce: str, sig: str) -> bool:
        if nonce not in self.nonces or self.public_key is None:
            return False
        self.nonces.discard(nonce)
        try:
            Ed25519PublicKey.from_public_bytes(self.public_key).verify(
                b64url_decode(sig), signing_payload(purpose, device_id, nonce)
            )
        except InvalidSignature:
            return False
        return True

    def handle(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path == "/device/v1/challenge":
            nonce = f"nonce-{len(self.nonces)}-{random.random():.12f}"
            self.nonces.add(nonce)
            return httpx.Response(200, json={"nonce": nonce, "expires_in": 300})
        if path == "/device/v1/register":
            body = json.loads(req.content)
            self.public_key = b64url_decode(body["public_key"])
            did = device_id_from_public_key(self.public_key)
            if not self._verify(Purpose.REGISTER, did, body["nonce"], body["signature"]):
                return httpx.Response(401, json={"detail": "bad signature"})
            self.registers += 1
            if self.registers <= self.register_unpaired:
                return httpx.Response(
                    200,
                    json={
                        "device_id": did,
                        "paired": False,
                        "pairing_code": "ABCDEF",
                        "expires_in": 900,
                    },
                )
            return httpx.Response(200, json={"device_id": did, "paired": True})
        if path == "/device/v1/token":
            body = json.loads(req.content)
            if not self._verify(Purpose.TOKEN, body["device_id"], body["nonce"], body["signature"]):
                return httpx.Response(401, json={"detail": "bad signature"})
            self.tokens += 1
            return httpx.Response(
                200, json={"access_token": f"tok{self.tokens}", "expires_in": 3600}
            )
        if path.startswith(("/device/v1/frames/", "/device/v1/products/")) and req.method == "PUT":
            assert req.headers["Authorization"].startswith("Bearer tok")
            self.uploads.append((path, req.url.params["variant"], req.content))
            self.upload_headers.append(dict(req.headers))
            return httpx.Response(204)
        return httpx.Response(404)

    async def ahandle(self, req: httpx.Request) -> httpx.Response:
        """For streamed bodies: read them first, then answer like `handle`."""
        await req.aread()
        return self.handle(req)

    async def ws_handler(self, ws: ServerConnection) -> None:
        assert ws.request is not None
        self.ws_auth.append(ws.request.headers["Authorization"])
        try:
            self.received.append(parse_envelope(await ws.recv()))  # hello
            sent: list[str] = []
            first = len(self.ws_auth) == 1  # commands only on the first connection
            for cmd in self.commands if first else []:
                env = Envelope.wrap(cmd, ts=TS)
                sent.append(env.id)
                await ws.send(env.model_dump_json())
            # Collect replies (status and frames may arrive in between), then a little more.
            deadline = asyncio.get_running_loop().time() + 5
            while asyncio.get_running_loop().time() < deadline:
                try:
                    env = parse_envelope(await asyncio.wait_for(ws.recv(), 0.5))
                except TimeoutError:
                    if len(self.replies) >= len(sent):
                        break
                    continue
                if isinstance(env.body, Ack | ErrorReply):
                    self.replies[env.body.ref] = env.body
                else:
                    self.received.append(env)
            if first:
                self.reply_order = [self.replies.get(i) for i in sent]
            await ws.close(self.close_code)
        except ConnectionClosed:
            pass
        finally:
            self.done.set()


def _store_with_frame(tmp_path: Path) -> tuple[ImageStore, str, str]:
    store = ImageStore(tmp_path / "data", TZ)
    s = store.save(np.zeros((90, 160, 3), dtype=np.uint8), TS)
    return store, s.night_id, s.name


def test_full_session_against_fake_hub(tmp_path: Path) -> None:
    store, night, name = _store_with_frame(tmp_path)
    live = LiveState()
    identity = DeviceIdentity.load_or_create(tmp_path / "device.key")
    hub = FakeHub()
    hub.commands = [
        Command(name=CommandName.UPLOAD_FRAME, args={"night_id": night, "name": name}),
        Command(
            name=CommandName.UPLOAD_FRAME,
            args={"night_id": night, "name": name, "variant": "thumb"},
        ),
        Command(name=CommandName.UPLOAD_FRAME, args={"night_id": night, "name": "gone.jpg"}),
        Command(name=CommandName.UPLOAD_FRAME, args={"night_id": "bad", "name": name}),
        Command(name=CommandName.FOCUS_MODE, args={"on": True}),
        Command(name=CommandName.RESTART),
    ]
    status = Status(mode=Mode.NIGHT, exposure_us=1, gain=0, mean=0.2, uptime_s=5, time_trusted=True)

    async def scenario() -> PairingState:
        async with serve(hub.ws_handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            cfg = HubConfig(hub_url=f"http://127.0.0.1:{port}", register_interval_s=0.01)
            pairing = PairingState(identity.device_id, cfg.hub_url, "sim", "0.1.0")
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(hub.handle), base_url=cfg.hub_url
            ) as http:
                session = HubSession(
                    cfg, identity, pairing, store, live, "sim", "0.1.0", lambda: status, http
                )
                stop = asyncio.Event()
                task = asyncio.create_task(session.run(stop))
                await asyncio.wait_for(hub.done.wait(), 10)
                # 4403 sends the client back to pairing; it registers again (now paired).
                await asyncio.sleep(0.2)
                stop.set()
                await asyncio.wait_for(task, 10)
                return pairing

    pairing = asyncio.run(scenario())

    # Pairing: two unpaired answers with a code, then paired; after 4403 it registers again
    # and reconnects with a new token.
    assert hub.registers >= 4
    assert len(hub.ws_auth) >= 2
    assert hub.ws_auth[1] == "Bearer tok2"
    assert hub.ws_auth[0] == "Bearer tok1"
    hello = hub.received[0].body
    assert isinstance(hello, Hello)
    assert hello.device_id == identity.device_id

    kinds = [(r.TYPE, getattr(r, "code", None)) if r else None for r in hub.reply_order]
    assert kinds == [
        ("ack", None),
        ("ack", None),
        ("error", "not_found"),
        ("error", "invalid_args"),
        ("ack", None),
        ("error", "unsupported"),
    ]
    full = (store.images_dir / night / name).read_bytes()
    thumb = (store.images_dir / night / "thumbnails" / name).read_bytes()
    assert hub.uploads == [
        (f"/device/v1/frames/{night}/{name}", "full", full),
        (f"/device/v1/frames/{night}/{name}", "thumb", thumb),
    ]
    assert live.focus_mode is True
    assert any(isinstance(e.body, Status) for e in hub.received)
    assert pairing.info().connected is False


def test_frames_are_sent_while_connected_and_focus_frames_skipped(tmp_path: Path) -> None:
    store, night, name = _store_with_frame(tmp_path)
    identity = DeviceIdentity.load_or_create(tmp_path / "device.key")
    hub = FakeHub(register_unpaired=0, close_code=1000)
    frame = FrameInfo(
        captured_at=TS,
        night_id=night,
        name=name,
        mode=Mode.NIGHT,
        exposure_us=1000,
        gain=0,
        mean=0.2,
        sun_elevation=-30,
        profile="sim",
    )
    focus = frame.model_copy(update={"name": "focus.jpg"})

    async def scenario() -> None:
        async with serve(hub.ws_handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            cfg = HubConfig(hub_url=f"http://127.0.0.1:{port}")
            pairing = PairingState(identity.device_id, cfg.hub_url, "sim", "0.1.0")
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(hub.handle), base_url=cfg.hub_url
            ) as http:
                session = HubSession(
                    cfg, identity, pairing, store, LiveState(), "sim", "0.1.0", lambda: None, http
                )
                session.offer_frame(frame)  # offline: dropped (SPEC §6.7)
                stop = asyncio.Event()
                task = asyncio.create_task(session.run(stop))
                while not pairing.info().connected:  # noqa: ASYNC110 - test polls shared state
                    await asyncio.sleep(0.01)
                session.offer_frame(focus)
                session.offer_frame(frame)
                await asyncio.wait_for(hub.done.wait(), 10)
                stop.set()
                await asyncio.wait_for(task, 10)

    asyncio.run(scenario())
    frames = [e.body for e in hub.received if isinstance(e.body, FrameInfo)]
    assert [f.name for f in frames] == [name]


@pytest.mark.parametrize("paired", [False, True])
def test_setup_endpoint(tmp_path: Path, paired: bool) -> None:
    pairing = PairingState("a" * 26, "https://allskyhub.org", "sim", "0.1.0")
    if paired:
        pairing.set_paired()
    else:
        pairing.set_unpaired("ABCDEF", 900, now=time.monotonic())
    web = WebServer(LiveState(), "127.0.0.1", 0, pairing)
    web.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{web.port}/api/setup", timeout=5) as r:
            body = json.loads(r.read())
    finally:
        web.stop()
    assert body["device_id"] == "a" * 26
    assert body["hub_url"] == "https://allskyhub.org"
    assert body["paired"] is paired
    assert body["pairing_code"] == (None if paired else "ABCDEF")


def test_products_announced_and_uploaded_streamed(tmp_path: Path) -> None:
    """SPEC §6.3/§6.5/§6.7: products after connect, upload_product streams the file."""
    store, night, _ = _store_with_frame(tmp_path)
    folder = store.night_dir(night)
    video = bytes(range(256)) * 9000  # ~2.3 MB, more than one 1 MB chunk
    (folder / "timelapse.mp4").write_bytes(video)
    (folder / "keogram.jpg").write_bytes(b"\xff\xd8keogram")
    (folder / "thumbnails" / "timelapse.jpg").write_bytes(b"\xff\xd8thumb")
    (folder / "products.json").write_text('{"duration_s": {"timelapse.mp4": 12.5}}')
    identity = DeviceIdentity.load_or_create(tmp_path / "device.key")
    hub = FakeHub(register_unpaired=0, close_code=1000)
    hub.commands = [
        Command(name=CommandName.UPLOAD_PRODUCT, args={"night_id": night, "name": "timelapse.mp4"}),
        Command(
            name=CommandName.UPLOAD_PRODUCT,
            args={"night_id": night, "name": "timelapse.mp4", "variant": "thumb"},
        ),
        Command(
            name=CommandName.UPLOAD_PRODUCT, args={"night_id": night, "name": "startrails.jpg"}
        ),
        Command(name=CommandName.UPLOAD_PRODUCT, args={"night_id": night, "name": "../x.jpg"}),
    ]

    async def scenario() -> None:
        async with serve(hub.ws_handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            cfg = HubConfig(hub_url=f"http://127.0.0.1:{port}")
            pairing = PairingState(identity.device_id, cfg.hub_url, "sim", "0.1.0")
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(hub.ahandle), base_url=cfg.hub_url
            ) as http:
                session = HubSession(
                    cfg, identity, pairing, store, LiveState(), "sim", "0.1.0",
                    lambda: None, http, latest_products=lambda: newest_products(store),
                )  # fmt: skip
                stop = asyncio.Event()
                task = asyncio.create_task(session.run(stop))
                await asyncio.wait_for(hub.done.wait(), 10)
                stop.set()
                await asyncio.wait_for(task, 10)

    asyncio.run(scenario())
    announced = [e.body for e in hub.received if isinstance(e.body, Products)]
    assert announced, "no products message after connect"
    files = {p.name: p for p in announced[0].products}
    assert set(files) == {"keogram.jpg", "timelapse.mp4"}
    assert files["timelapse.mp4"].size == len(video)
    assert files["timelapse.mp4"].duration_s == 12.5
    assert files["timelapse.mp4"].thumbnail is True
    assert files["keogram.jpg"].thumbnail is False

    kinds = [(r.TYPE, getattr(r, "code", None)) if r else None for r in hub.reply_order]
    assert kinds == [
        ("ack", None),
        ("ack", None),
        ("error", "not_found"),
        ("error", "invalid_args"),
    ]
    # Product uploads run concurrently, so they may finish in any order.
    by_variant = {u[1]: (u, h) for u, h in zip(hub.uploads, hub.upload_headers, strict=True)}
    (path, _, body), headers = by_variant["full"]
    assert (path, body) == (f"/device/v1/products/{night}/timelapse.mp4", video)
    assert headers["content-type"] == "video/mp4"
    assert headers["content-length"] == str(len(video))
    (_, _, body), headers = by_variant["thumb"]
    assert body == b"\xff\xd8thumb"
    assert headers["content-type"] == "image/jpeg"
