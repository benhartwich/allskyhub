"""The Allsky module ``allsky_sharedsky`` (SPEC §10.4) against the hub's own connect/v1: the
module runs as in Allsky's flow, with Allsky's helpers replaced, and its requests go to the
test app."""

from __future__ import annotations

import asyncio
import datetime as dt
import importlib.util
import io
import json
import os
import sys
import time
import types
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest
from fastapi import FastAPI
from PIL import Image, ImageDraw
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices import station_solve, stations
from allskyhub_server.devices.stations import SecretKey
from allskyhub_server.models import Device, EventRecord, Frame, User
from allskyhub_solve import Solution
from tests.helpers import make_account

MODULE = Path(__file__).resolve().parents[2] / "integrations/allsky_sharedsky/allsky_sharedsky.py"
W, H = 1200, 900


def _jpeg(width: int = W, height: int = H, streak: bool = False) -> bytes:
    im = Image.new("RGB", (width, height), (12, 14, 20))
    if streak:
        ImageDraw.Draw(im).line((100, 100, 500, 300), fill=(250, 250, 250), width=3)
    out = io.BytesIO()
    im.save(out, "JPEG", quality=90)
    return out.getvalue()


def _encode(image: Any, longest: int | None, limit: int) -> bytes | None:
    """The module's JPEG encoder with Pillow instead of OpenCV."""
    im = Image.fromarray(image)
    if longest:
        im.thumbnail((longest, longest))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=80)
    return out.getvalue() if out.tell() <= limit else None


class _Allsky:
    """Allsky's ``allsky_shared`` and ``allsky_base`` as far as the module uses them."""

    def __init__(self, home: Path) -> None:
        self.logs: list[tuple[int, str]] = []
        self.env: dict[str, str] = {
            "AS_TIMESTAMP": str(int(time.time()) - 40),
            "AS_EXPOSURE_US": "30000000",
            "AS_GAIN": "200",
            "ALLSKY_VERSION": "v2025.01.01",
            "ALLSKY_WEBSITE": str(home / "html" / "allsky"),
        }
        shared = types.ModuleType("allsky_shared")
        shared.image = np.full((H, W, 3), 15, dtype=np.uint8)  # type: ignore[attr-defined]
        shared.TOD = "night"  # type: ignore[attr-defined]
        shared.ALLSKYPATH = str(home)  # type: ignore[attr-defined]
        shared.get_environment_variable = self.env.get  # type: ignore[attr-defined]
        shared.get_setting = {"location": "Sternwarte Musterberg"}.get  # type: ignore[attr-defined]
        shared.get_lat_lon = lambda: (48.3, 14.29)  # type: ignore[attr-defined]
        shared.get_allsky_variable = {"AS_STARCOUNT": "412"}.get  # type: ignore[attr-defined]
        shared.network_ok = lambda name: None  # type: ignore[attr-defined]
        shared.network_failure = self._failure  # type: ignore[attr-defined]
        self.shared = shared
        base = types.ModuleType("allsky_base")
        logs = self.logs

        class Base:
            def __init__(self, params: dict[str, Any], event: str) -> None:
                self.params = params
                self.event = event
                self.debug_mode = bool(params.get("ALLSKYTESTMODE"))

            def get_param(
                self, param: str, default: Any, target_type: type = str, blank: bool = False
            ) -> Any:
                value = self.params.get(param, default)
                if target_type is bool and isinstance(value, str):
                    return value.strip().lower() in ("true", "1", "yes", "on")
                try:
                    value = target_type(value)
                except (TypeError, ValueError):
                    value = default
                return default if target_type is str and blank and value == "" else value

            def log(self, level: int, message: str, **_: Any) -> None:
                logs.append((level, message))

        base.ALLSKYMODULEBASE = Base  # type: ignore[attr-defined]
        self.base = base

    def _failure(self, name: str, message: str, secrets: Any = None) -> None:
        self.logs.append((1, message))


def _load(monkeypatch: pytest.MonkeyPatch, allsky: _Allsky) -> Any:
    monkeypatch.setitem(sys.modules, "allsky_shared", allsky.shared)
    monkeypatch.setitem(sys.modules, "allsky_base", allsky.base)
    spec = importlib.util.spec_from_file_location("allsky_sharedsky", MODULE)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cls: Any = module.ALLSKYSHAREDSKY
    cls.encode = staticmethod(_encode)
    cls.clock = staticmethod(lambda: True)
    return module


async def _station(app: FastAPI, client: httpx.AsyncClient) -> tuple[str, str]:
    email = await make_account(client)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        user = await db.scalar(select(User).where(User.email == email))
        assert user is not None
        device, secret = await stations.create(
            db, user, "Sternwarte Musterberg", "allsky", SecretKey(app.state.stores.vault)
        )
        await db.commit()
        return device.id, secret


def _bridge(client: httpx.AsyncClient) -> Any:
    """The module's transport, from its thread into the test app."""
    loop = asyncio.get_running_loop()

    def transport(
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json_body: dict[str, Any] | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        request = client.request(
            method, url.removeprefix("https://hub.example"), headers=headers, json=json_body,
            files=files,
        )  # fmt: skip
        r = asyncio.run_coroutine_threadsafe(request, loop).result(30)
        return r.status_code, r.json() if r.content else {}

    return staticmethod(transport)


def test_jpeg_size(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    module = _load(monkeypatch, _Allsky(tmp_path))
    assert module.jpeg_size(_jpeg(640, 480)) == (640, 480)
    out = io.BytesIO()
    Image.new("RGB", (300, 200)).save(out, "JPEG", progressive=True)
    assert module.jpeg_size(out.getvalue()) == (300, 200)
    assert module.jpeg_size(b"not a jpeg") is None


@pytest.mark.parametrize(("mirrored", "tilt"), [(True, (2.5, -1.2)), (False, (0.0, 0.0))])
def test_sky_to_pixel_is_the_solvers_lens(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mirrored: bool, tilt: tuple[float, float]
) -> None:
    """The module's own drawing of the hub's orientation (for Allsky's overlay) puts every
    direction where the hub's solver does (SPEC §4.8)."""
    module = _load(monkeypatch, _Allsky(tmp_path))
    sol = Solution(
        cx=610.0, cy=445.0, a1=560.0, a3=-35.0, rot=37.0, flip=-1.0 if mirrored else 1.0,
        tilt_east=tilt[0], tilt_north=tilt[1], stars=60, rms_px=0.0, rms_deg=0.2,
        width=W, height=H,
    )  # fmt: skip
    found = station_solve.to_orientation(sol, dt.datetime.now(dt.UTC))
    for alt, az in ((90.0, 0.0), (45.0, 10.0), (20.0, 135.0), (5.0, 300.0)):
        x, y = sol.project(np.array([alt]), np.array([az]))
        px = module.sky_to_pixel(found, alt, az)
        assert px is not None
        assert px[0] == pytest.approx(float(x[0]), abs=1e-6)
        assert px[1] == pytest.approx(float(y[0]), abs=1e-6)
        half = module.sky_to_pixel(found, alt, az, W // 2)
        assert half[0] == pytest.approx(float(x[0]) / 2, abs=1e-6)
    assert module.sky_to_pixel(None, 45.0, 0.0) is None
    assert module.sky_to_pixel({**found, "lens": None}, 45.0, 0.0) is None


def test_meteor_in_the_hour_repeated_when_the_clocks_go_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The file name's local time maps to two moments an hour apart; the module's own frame
    of that exposure tells which (allsky's review of #427)."""
    module = _load(monkeypatch, _Allsky(tmp_path))
    stamp = "20261025023010"
    guess = int(time.mktime(time.strptime(stamp, "%Y%m%d%H%M%S")))
    entry = {"time": stamp, "file": f"meteors-{stamp}.jpg", "p1": [1, 2], "p2": [3, 4]}
    (tmp_path / f"meteors-{stamp}.json").write_text(json.dumps([entry]))
    seen = {guess - 3600: 20.0}
    [(key, meta, image)] = list(module.meteors(tmp_path, 0.0, {}, seen.get, True))
    assert key == f"{stamp}:0"
    assert meta["start"] == module.iso(guess - 3600)
    assert meta["end"] == module.iso(guess - 3600 + 20)
    assert image == tmp_path / f"meteors-{stamp}.jpg"


async def test_module_sends_station_images_and_meteors(
    app: FastAPI,
    client: httpx.AsyncClient,
    new_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def daylight(*_: object) -> float:  # no full frames asked for solving (§10.1)
        return 20.0

    monkeypatch.setattr(station_solve, "sun_altitude", daylight)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    allsky = _Allsky(tmp_path)
    module = _load(monkeypatch, allsky)
    module.ALLSKYSHAREDSKY.transport = _bridge(new_client)
    station_id, secret = await _station(app, client)
    params = {"stationid": station_id, "secret": secret, "elevation": "300",
              "hub": "https://hub.example", "meteors": "true"}  # fmt: skip

    # Wrong secret: an error in the WebUI, nothing sent.
    wrong = await asyncio.to_thread(module.sharedsky, {**params, "secret": "0" * 64}, "night")
    assert wrong == "Station not sent"
    assert any(level == 0 and "station ID and secret" in m for level, m in allsky.logs)

    # The first image: the station's data, then a thumbnail.
    assert await asyncio.to_thread(module.sharedsky, params, "night") == "Sent station, image"
    async with maker() as db:
        device = await db.get(Device, station_id)
        assert device is not None
        assert device.last_status is not None
        station = device.last_status["station"]
        assert (station["width"], station["height"], station["software"]) == (W, H, "Allsky")
        assert device.last_status["settings"]["elevation_m"] == 300
        frames = (await db.scalars(select(Frame).where(Frame.device_id == station_id))).all()
        assert len(frames) == 1
        assert frames[0].mode == "night"
        start = int(allsky.env["AS_TIMESTAMP"])
        assert frames[0].captured_at == dt.datetime.fromtimestamp(start + 15, dt.UTC)
        assert (frames[0].exposure_us, frames[0].gain) == (30_000_000, 200)

    # The meteor module saved three meteors: one at the frame's size, one smaller, and one
    # on an image this module did not see (no exposure known: it ends where it starts).
    folder = tmp_path / "html" / "allsky" / "meteors"
    folder.mkdir(parents=True)
    stamp = time.strftime("%Y%m%d%H%M%S", time.localtime(start))
    small = time.strftime("%Y%m%d%H%M%S", time.localtime(start - 60))
    unseen = time.strftime("%Y%m%d%H%M%S", time.localtime(start - 120))
    saved = ((stamp, _jpeg(streak=True)), (small, _jpeg(640, 480)), (unseen, _jpeg(streak=True)))
    for name, image in saved:
        (folder / f"meteors-{name}.jpg").write_bytes(image)
        sidecar = folder / f"meteors-{name}.json"
        entry = {"time": name, "file": f"meteors-{name}.jpg", "p1": [100, 100], "p2": [500, 300]}
        sidecar.write_text(json.dumps([entry]))
        os.utime(sidecar, (time.time() + 1, time.time() + 1))
    (folder / "meteors.json").write_text("[]")  # the meteor module's rolling log

    assert await asyncio.to_thread(module.sharedsky, params, "night") == "Sent meteor, meteor"
    assert any("skips the meteor" in m for _, m in allsky.logs)
    async with maker() as db:
        events = (
            await db.scalars(select(EventRecord).where(EventRecord.device_id == station_id))
        ).all()
        assert len(events) == 2
        first, last = sorted(events, key=lambda e: e.start)
        assert first.start == first.end == dt.datetime.fromtimestamp(start - 120, dt.UTC)
        assert last.data["track_px"] == [[100.0, 100.0], [500.0, 300.0]]
        assert last.start == dt.datetime.fromtimestamp(start, dt.UTC)
        assert last.end == dt.datetime.fromtimestamp(start + 30, dt.UTC)
        assert last.has_original

    # Sent once only; the next image waits for the interval.
    assert await asyncio.to_thread(module.sharedsky, params, "night") == "Nothing to send"

    # Allsky's test button: the hub's test mode, nothing stored.
    tested = await asyncio.to_thread(module.sharedsky, {**params, "ALLSKYTESTMODE": True}, "night")
    assert tested == "Sent station, image"
    async with maker() as db:
        frames = (await db.scalars(select(Frame).where(Frame.device_id == station_id))).all()
        assert len(frames) == 1
    state = json.loads((tmp_path / "config" / "sharedsky" / "state.json").read_text())
    assert sorted(state["sent"]) == [f"{unseen}:0", f"{small}:0", f"{stamp}:0"]
