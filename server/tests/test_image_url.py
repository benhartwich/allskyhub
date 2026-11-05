"""Stations by their image address (SharedSky, SPEC §10.7): the safe fetcher, the time of
an image, the interval, the overlay mask, and the hub's loop."""

from __future__ import annotations

import datetime as dt
import io
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest
from fastapi import FastAPI
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_protocol import FrameVariant
from allskyhub_server.devices import image_url, station_detect, stations
from allskyhub_server.devices.image_url import Fetcher, FetchError
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.devices.stations import SecretKey
from allskyhub_server.mail.send import MemoryMailer
from allskyhub_server.models import Device, EventRecord, Frame, StationSource, User
from tests.helpers import make_account
from tests.test_station_detect import frame as _frame_with

PUBLIC = "93.184.216.34"
NAMES = {"cam.example.org": [PUBLIC], "intern.example.org": ["10.0.0.5"]}


def _jpeg(width: int = 640, height: int = 480, shade: int = 20, exif: bool = True) -> bytes:
    im = Image.new("RGB", (width, height), (shade, shade, shade + 5))
    out = io.BytesIO()
    if exif:
        data = Image.Exif()
        sub = data.get_ifd(0x8769)
        sub[36867] = "2026:11:05 21:40:00"
        sub[36881] = "+01:00"
        im.save(out, "JPEG", exif=data)
    else:
        im.save(out, "JPEG")
    return out.getvalue()


async def _resolve(host: str, port: int) -> list[str]:
    if host not in NAMES:
        raise OSError("unknown")
    return NAMES[host]


def _fetcher(handler: Any) -> Fetcher:
    return Fetcher(resolve=_resolve, transport=httpx.MockTransport(handler))


async def test_pinned_to_the_checked_address() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=_jpeg(), headers={"content-type": "image/jpeg",
                                                              "etag": '"a1"'})  # fmt: skip

    found = await _fetcher(handler).get("https://cam.example.org/allsky/image.jpg")
    assert found.new
    assert found.etag == '"a1"'
    request = seen[0]
    assert request.url.host == PUBLIC  # the connection goes to the address checked
    assert request.url.port in (None, 443)  # httpx leaves out the scheme's own port
    assert request.headers["host"] == "cam.example.org"  # the name stays for TLS and Host
    assert request.extensions["sni_hostname"] == "cam.example.org"
    assert request.headers["user-agent"].startswith("SharedSky")


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("https://intern.example.org/image.jpg", "not_public"),
        ("http://127.0.0.1/image.jpg", "not_public"),
        ("http://[::1]/image.jpg", "not_public"),
        ("http://169.254.169.254/latest/meta-data", "not_public"),
        ("https://cam.example.org:8443/image.jpg", "bad_url"),
        ("https://user:pw@cam.example.org/image.jpg", "bad_url"),
        ("ftp://cam.example.org/image.jpg", "bad_url"),
        ("https://unknown.example.org/image.jpg", "unreachable"),
    ],
)
async def test_only_the_public_internet(url: str, code: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("never connected")

    with pytest.raises(FetchError) as caught:
        await _fetcher(handler).get(url)
    assert caught.value.code == code


async def test_redirects_are_checked_too() -> None:
    def to_inside(request: httpx.Request) -> httpx.Response:
        assert request.url.host == PUBLIC
        return httpx.Response(302, headers={"location": "https://intern.example.org/x.jpg"})

    with pytest.raises(FetchError) as caught:
        await _fetcher(to_inside).get("https://cam.example.org/image.jpg")
    assert caught.value.code == "not_public"

    def to_plain(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://cam.example.org/x.jpg"})

    with pytest.raises(FetchError) as caught:  # never from https down to http
        await _fetcher(to_plain).get("https://cam.example.org/image.jpg")
    assert caught.value.code == "insecure_redirect"

    def loop(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "/again.jpg"})

    with pytest.raises(FetchError) as caught:
        await _fetcher(loop).get("https://cam.example.org/image.jpg")
    assert caught.value.code == "too_many_redirects"


async def test_rebinding_cannot_lead_elsewhere() -> None:
    """The name answers public first and private after: the hub connects to the first
    answer it checked and never looks it up again for that request."""
    answers = [[PUBLIC], ["10.0.0.5"]]
    hosts: list[str] = []

    async def rebinding(host: str, port: int) -> list[str]:
        return answers.pop(0)

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        return httpx.Response(200, content=_jpeg(), headers={"content-type": "image/jpeg"})

    fetcher = Fetcher(resolve=rebinding, transport=httpx.MockTransport(handler))
    await fetcher.get("https://cam.example.org/image.jpg")
    assert hosts == [PUBLIC]
    with pytest.raises(FetchError):  # the second fetch looks it up again and refuses
        await fetcher.get("https://cam.example.org/image.jpg")


@pytest.mark.parametrize(
    ("headers", "body", "code"),
    [
        ({"content-type": "text/html"}, b"<html>", "wrong_type"),
        ({"content-type": "image/jpeg", "content-length": str(9 * 1024 * 1024)}, b"x", "too_large"),
        ({"content-type": "image/jpeg"}, b"x" * (image_url.MAX_BYTES + 1), "too_large"),
    ],
)
async def test_what_comes_back(headers: dict[str, str], body: bytes, code: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers=headers)

    with pytest.raises(FetchError) as caught:
        await _fetcher(handler).get("https://cam.example.org/image.jpg")
    assert caught.value.code == code


async def test_unchanged_and_busy() -> None:
    def unchanged(request: httpx.Request) -> httpx.Response:
        assert request.headers["if-none-match"] == '"a1"'
        return httpx.Response(304)

    found = await _fetcher(unchanged).get("https://cam.example.org/i.jpg", etag='"a1"')
    assert not found.new

    def busy(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "120"})

    with pytest.raises(FetchError) as caught:
        await _fetcher(busy).get("https://cam.example.org/i.jpg")
    assert (caught.value.code, caught.value.retry_after) == ("busy", 120)


def test_images_and_their_time(monkeypatch: pytest.MonkeyPatch) -> None:
    fetched = dt.datetime(2026, 11, 5, 21, 0, tzinfo=dt.UTC)
    prepared = image_url.prepare(_jpeg(), "image/jpeg")
    assert (prepared.width, prepared.height) == (640, 480)
    assert image_url.frame_time(prepared, None, None, fetched) == (
        dt.datetime(2026, 11, 5, 20, 40, tzinfo=dt.UTC), "exif",
    )  # fmt: skip
    no_offset = image_url.Prepared(prepared.jpeg, 640, 480, "2026:11:05 21:40:00", None)
    assert image_url.frame_time(no_offset, "Europe/Vienna", None, fetched)[1] == "exif_zone"
    plain = image_url.prepare(_jpeg(exif=False), "image/jpeg")
    when, origin = image_url.frame_time(plain, None, "Thu, 05 Nov 2026 20:39:30 GMT", fetched)
    assert (when.minute, origin) == (39, "last_modified")
    assert image_url.frame_time(plain, None, None, fetched) == (fetched, "fetch")
    # A PNG becomes a JPEG for the frame path; a big one is refused before decoding.
    out = io.BytesIO()
    Image.new("RGB", (200, 100)).save(out, "PNG")
    png = image_url.prepare(out.getvalue(), "image/png")
    assert png.jpeg[:2] == b"\xff\xd8"
    monkeypatch.setattr(image_url, "PNG_MAX_PIXELS", 10_000)
    with pytest.raises(FetchError) as caught:
        image_url.prepare(out.getvalue(), "image/png")
    assert caught.value.code == "too_large"
    with pytest.raises(FetchError):
        image_url.prepare(out.getvalue(), "image/jpeg")  # says JPEG, is PNG


def test_interval_and_backoff() -> None:
    assert image_url.next_interval([]) == 30
    assert image_url.next_interval([0, 60, 120, 180]) == 30
    assert image_url.next_interval([0, 300, 600, 900]) == 150
    assert image_url.next_interval([0, 1200, 2400, 3600]) == 300
    assert [image_url.backoff(n) for n in (1, 2, 3, 8)] == [60, 120, 240, 3600]


def test_overlay_mask(tmp_path: Path) -> None:
    masks = image_url.OverlayMask(tmp_path)
    for i in range(25):
        im = Image.new("L", (640, 480), 20)
        im.paste(255 if i % 2 else 0, (0, 0, 160, 40))  # a clock that changes every frame
        out = io.BytesIO()
        im.convert("RGB").save(out, "JPEG")
        masks.update("dev", out.getvalue())
    mask = masks.mask("dev")
    assert mask is not None
    assert bool(mask[5, 5])  # the overlay
    assert not bool(mask[200, 200])  # the sky
    assert float(np.mean(mask)) < 0.1
    masks.forget("dev")
    assert masks.mask("dev") is None


async def _approved_station(app: FastAPI, client: httpx.AsyncClient) -> str:
    email = await make_account(client)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        user = await db.scalar(select(User).where(User.email == email))
        assert user is not None
        device, secret = await stations.create(
            db, user, "Gartensternwarte", "image-url", SecretKey(app.state.stores.vault)
        )
        assert secret == ""  # nothing to send, nothing to sign
        device.last_status = {"settings": {"latitude": 48.3, "longitude": 14.29,
                                           "timezone": "Europe/Vienna"}}  # fmt: skip
        db.add(StationSource(device_id=device.id, url="https://cam.example.org/image.jpg",
                             state="approved"))  # fmt: skip
        await db.commit()
        return device.id


async def test_the_loop_fetches_approved_stations(
    app: FastAPI, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    device_id = await _approved_station(app, client)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    store: ImageStore = app.state.images
    mailer: MemoryMailer = app.state.mailer
    state = {"status": 200}

    def handler(request: httpx.Request) -> httpx.Response:
        if state["status"] == 304:
            return httpx.Response(304)
        if state["status"] != 200:
            return httpx.Response(state["status"])
        return httpx.Response(200, content=_jpeg(), headers={"content-type": "image/jpeg",
                                                              "etag": '"v1"'})  # fmt: skip

    ctx = image_url.Context(maker, store, mailer, "https://allskyhub.org", _fetcher(handler),
                            image_url.OverlayMask(tmp_path / "masks"),
                            station_detect.Detector(tmp_path / "detect"))  # fmt: skip
    now = dt.datetime(2026, 11, 5, 20, 41, tzinfo=dt.UTC)
    assert await image_url.round_once(ctx, now) == 1
    async with maker() as db:
        source = await db.get(StationSource, device_id)
        device = await db.get(Device, device_id)
        frames = (await db.scalars(select(Frame).where(Frame.device_id == device_id))).all()
    assert source is not None
    assert device is not None
    assert len(frames) == 1
    assert frames[0].captured_at == dt.datetime(2026, 11, 5, 20, 40, tzinfo=dt.UTC)
    assert (device.last_frame or {})["time_source"] == "exif"
    assert (device.last_status or {})["station"]["width"] == 640
    assert source.etag == '"v1"'
    assert source.next_fetch_at == now + dt.timedelta(seconds=30)
    assert store.path(device_id, FrameVariant.THUMB).is_file()
    # Not due yet; then unchanged: nothing new.
    assert await image_url.round_once(ctx, now + dt.timedelta(seconds=10)) == 0
    state["status"] = 304
    assert await image_url.round_once(ctx, now + dt.timedelta(seconds=31)) == 1
    async with maker() as db:
        assert len((await db.scalars(select(Frame).where(Frame.device_id == device_id))).all()) == 1
    # Errors back off; after a day the station pauses and its owner hears of it.
    state["status"] = 500
    later = now + dt.timedelta(minutes=2)
    await image_url.round_once(ctx, later)
    async with maker() as db:
        source = await db.get(StationSource, device_id)
        assert source is not None
        assert source.errors == 1
        assert source.next_fetch_at == later + dt.timedelta(seconds=60)
        source.errors_since = later - dt.timedelta(hours=25)
        await db.commit()
    await image_url.round_once(ctx, later + dt.timedelta(minutes=2))
    async with maker() as db:
        source = await db.get(StationSource, device_id)
        assert source is not None
        assert source.state == "paused"
    assert any("pausiert" in m.subject for m in mailer.sent)


async def test_failed_orientation_sends_it_back(
    app: FastAPI, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    device_id = await _approved_station(app, client)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        device = await db.get(Device, device_id)
        assert device is not None
        device.last_status = dict(device.last_status or {}) | {
            "solve": {"fails": ["20261101", "20261102", "20261103"]}
        }
        await db.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no fetch while it waits for the admin")

    mailer: MemoryMailer = app.state.mailer
    ctx = image_url.Context(maker, app.state.images, mailer, "https://allskyhub.org",
                            _fetcher(handler), image_url.OverlayMask(tmp_path),
                            station_detect.Detector(tmp_path / "detect"))  # fmt: skip
    await image_url.round_once(ctx, dt.datetime(2026, 11, 5, 21, tzinfo=dt.UTC))
    async with maker() as db:
        source = await db.get(StationSource, device_id)
        assert source is not None
        assert source.state == "pending"
    assert any("neu geprüft" in m.subject for m in mailer.sent)


async def test_a_meteor_from_fetched_frames(
    app: FastAPI, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    """Three frames in a row, a streak only in the middle one: a trial event of the middle
    frame, with its track in full pixels, never paired."""
    device_id = await _approved_station(app, client)
    frames = [
        _frame_with(1600, 1200, seed=1),
        _frame_with(1600, 1200, (300, 300, 1100, 700), seed=2),
        _frame_with(1600, 1200, seed=3),
    ]
    served = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        n = served["n"]
        served["n"] += 1
        headers = {"content-type": "image/jpeg", "etag": f'"{n}"',
                   "last-modified": f"Thu, 05 Nov 2026 20:4{n}:00 GMT"}  # fmt: skip
        return httpx.Response(200, content=frames[n], headers=headers)

    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    ctx = image_url.Context(maker, app.state.images, app.state.mailer, "https://allskyhub.org",
                            _fetcher(handler), image_url.OverlayMask(tmp_path / "masks"),
                            station_detect.Detector(tmp_path / "detect"))  # fmt: skip
    now = dt.datetime(2026, 11, 5, 20, 40, 5, tzinfo=dt.UTC)
    for i in range(3):
        async with maker() as db:
            source = await db.get(StationSource, device_id)
            assert source is not None
            source.next_fetch_at = None
            source.interval_s = 60  # as if it had learnt the camera's minute
            await db.commit()
        await image_url.round_once(ctx, now + dt.timedelta(minutes=i))
    async with maker() as db:
        events = (
            await db.scalars(select(EventRecord).where(EventRecord.device_id == device_id))
        ).all()
    assert len(events) == 1
    data = events[0].data or {}
    assert (data["source"], data["trial"], data["time_trusted"]) == ("image_url", True, False)
    assert events[0].start == dt.datetime(2026, 11, 5, 20, 41, tzinfo=dt.UTC)  # the middle one
    assert data["track_px"][0] == pytest.approx([300, 300], abs=25)
    assert events[0].has_original


@pytest.mark.parametrize(
    ("ip", "ok"),
    [
        ("93.184.216.34", True),
        ("::ffff:10.0.0.5", False),
        ("64:ff9b::a00:5", False),  # NAT64 to 10.0.0.5
        ("64:ff9b::5db8:d822", True),  # NAT64 to 93.184.216.34
        ("2002:a00:5::1", False),  # 6to4 of 10.0.0.5
        ("2606:4700::1111", True),
    ],
)
def test_ipv4_inside_ipv6_counts_as_that_ipv4(ip: str, ok: bool) -> None:
    """allsky's review of #399: NAT64 and 6to4 can reach private IPv4 addresses."""
    assert image_url.public(ip, frozenset()) is ok


def test_proof_pictures_stay_small(tmp_path: Path) -> None:
    big = Image.new("L", (6000, 5000))  # 30 MP
    out = io.BytesIO()
    big.save(out, "PNG", optimize=True)
    with pytest.raises(FetchError) as caught:
        image_url.store_proof(tmp_path, "st", out.getvalue())
    assert caught.value.code == "too_large"
    image_url.store_proof(tmp_path, "st", _jpeg(4000, 3000))
    with Image.open(image_url.proof_path(tmp_path, "st")) as kept:
        assert max(kept.size) <= image_url.PROOF_EDGE
