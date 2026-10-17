"""Camera health (roadmap C14): checks, card and notifications once per change."""

from __future__ import annotations

import datetime as dt

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices import health
from allskyhub_server.models import Device, PushToken, User
from allskyhub_server.push.notify import Notifier
from tests.fake_device import FakeDevice
from tests.test_push import FakeSender
from tests.ws_helpers import paired_token

NOW = dt.datetime(2026, 10, 13, 22, tzinfo=dt.UTC)


def _device(**values: object) -> Device:
    d = Device(id="x" * 26, name="Garten", last_seen_at=NOW - dt.timedelta(minutes=1))
    d.last_status = {"time_trusted": True, "disk_free_pct": 50, "settings": {"night_delay_s": 5}}
    d.last_frame = {"captured_at": (NOW - dt.timedelta(minutes=1)).isoformat()}
    for k, v in values.items():
        setattr(d, k, v)
    return d


def test_checks() -> None:
    assert health.check(_device(), True, NOW) == []
    offline = health.check(_device(last_seen_at=NOW - dt.timedelta(hours=3)), False, NOW)
    assert [(p.key, p.text) for p in offline] == [
        ("offline", "Die Kamera ist offline seit 3 Stunden.")
    ]
    assert health.check(_device(last_seen_at=NOW - dt.timedelta(minutes=5)), False, NOW) == []
    stale = _device(last_frame={"captured_at": (NOW - dt.timedelta(hours=1)).isoformat()})
    assert [p.key for p in health.check(stale, True, NOW)] == ["no_frames"]
    # A long pause between frames (day, 30 min) is not "stuck".
    slow = _device(
        last_status={"time_trusted": True, "settings": {"day_delay_s": 1800}},
        last_frame={"captured_at": (NOW - dt.timedelta(minutes=40)).isoformat()},
    )
    assert health.check(slow, True, NOW) == []
    bad = _device(
        last_status={"time_trusted": False, "disk_free_pct": 8, "sensor_temp_c": 61,
                     "cpu_temp_c": 85, "update": {"state": "rolled_back"}}
    )  # fmt: skip
    problems = {p.key: p.level for p in health.check(bad, True, NOW)}
    assert problems == {"disk": "error", "sensor_hot": "warn", "cpu_hot": "warn",
                        "clock": "warn", "update": "warn"}  # fmt: skip
    # SPEC §4.1: no pictures while the owner paused capture is not a problem.
    stopped = _device(
        last_status={"time_trusted": True, "capture_paused": True},
        last_frame={"captured_at": (NOW - dt.timedelta(hours=2)).isoformat()},
    )
    assert health.check(stopped, True, NOW) == []
    assert health.paused(stopped)
    # SPEC §8: updates waiting for the reboot by day are shown, but are no problem.
    waiting = _device(last_status={"time_trusted": True, "reboot_required": True})
    assert health.reboot_required(waiting)
    assert not health.reboot_required(stopped)
    # SPEC §6.3 alpaca_error: shown with the configured port; unknown values as they are.
    taken = _device(last_status={"alpaca_error": "port_in_use",
                                 "settings": {"alpaca_port": 11111}})  # fmt: skip
    [problem] = health.check(taken, True, NOW)
    assert problem.key == "alpaca"
    assert problem.text.startswith("Alpaca-Port belegt: Port 11111 oder UDP 32227")
    odd = _device(last_status={"alpaca_error": "bind_failed"})
    assert [p.text for p in health.check(odd, True, NOW)] == [
        "Die Alpaca-Schnittstelle meldet einen Fehler (bind_failed)."
    ]


class _Online:
    def is_online(self, device_id: str) -> bool:
        return True


async def test_notified_once_per_change(app: FastAPI, client: httpx.AsyncClient) -> None:
    dev = FakeDevice()
    await paired_token(client, dev)
    sender = FakeSender()
    notifier = Notifier(app.state.sessionmaker, sender)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    now = dt.datetime.now(dt.UTC)
    fresh = {"captured_at": now.isoformat()}
    async with maker() as db:
        device = await db.get(Device, dev.device_id)
        assert device
        await db.execute(update(User).where(User.id == device.owner_id).values(notify_offline=True))
        db.add(PushToken(user_id=device.owner_id, token=f"t-{dev.device_id}", platform="ios"))
        await db.execute(
            update(Device)
            .where(Device.id == dev.device_id)
            .values(last_frame=fresh, last_status={"time_trusted": True, "disk_free_pct": 5})
        )
        await db.commit()
    registry = _Online()
    await notifier.check_health(registry, now)  # type: ignore[arg-type]
    await notifier.check_health(registry, now)  # type: ignore[arg-type]
    assert [(m.title, m.body) for _, m in sender.sent] == [
        ("Garten: Problem", "Nur noch 5 % Speicher frei.")
    ]
    async with maker() as db:
        await db.execute(
            update(Device)
            .where(Device.id == dev.device_id)
            .values(last_status={"time_trusted": True, "disk_free_pct": 50})
        )
        await db.commit()
    await notifier.check_health(registry, now)  # type: ignore[arg-type]
    assert sender.sent[-1][1].title == "Garten ist wieder in Ordnung"
    assert len(sender.sent) == 2


async def test_camera_page_shows_health(app: FastAPI, client: httpx.AsyncClient) -> None:
    dev = FakeDevice()
    await paired_token(client, dev)
    page = (await client.get(f"/cameras/{dev.device_id}")).text
    assert "Zustand" in page
    assert "Die Kamera ist offline" in page  # never connected


def test_dome_hint() -> None:
    """Roadmap C12: forecast clear, camera overcast for two hours."""
    now = dt.datetime(2026, 10, 13, 23, 30, tzinfo=dt.UTC)
    samples = [(now - dt.timedelta(minutes=10 * i), 0.95) for i in range(12)]
    clear = {dt.datetime(2026, 10, 13, h, tzinfo=dt.UTC): 5 for h in range(20, 24)}
    hint = health.dome_hint(samples, clear, now)
    assert hint is not None
    assert hint.key == "dome"
    # Clouds in the forecast, a clear moment, too few samples or no forecast: no hint.
    assert health.dome_hint(samples, dict.fromkeys(clear, 60), now) is None
    assert health.dome_hint([*samples[:-1], (now, 0.2)], clear, now) is None
    assert health.dome_hint(samples[:3], clear, now) is None
    assert health.dome_hint(samples, {}, now) is None


def _with_checks(*checks: tuple[str, str], disk: float = 50) -> Device:
    """SPEC §6.3 ``checks``: (id, state) pairs, detail "<id> tun"."""
    items = [{"id": i, "state": s, "detail": f"{i} tun"} for i, s in checks]
    return _device(last_status={"time_trusted": True, "disk_free_pct": disk, "checks": items})


def test_setup_card_and_check_warnings() -> None:
    assert health.setup_steps(_device()) == []  # an older agent: no checks, no card
    done = _with_checks(("frames", "ok"), ("mask", "ok"), ("orientation", "ok"))
    assert health.setup_steps(done) == []
    fresh = _with_checks(
        ("power", "ok"), ("frames", "ok"), ("mask", "warn"), ("orientation", "fail")
    )
    assert [(s.title, s.state, s.detail) for s in health.setup_steps(fresh)] == [
        ("Bilder kommen an", "ok", "frames tun"),
        ("Himmelsmaske gelernt", "warn", "mask tun"),
        ("Ausrichtung (Norden) bestimmt", "fail", "orientation tun"),
    ]
    # Setup steps are not repeated as warnings; other fails are, by their detail, unknown
    # ids too; what the hub says itself (free disk) is said once.
    many = _with_checks(
        ("orientation", "fail"), ("power", "fail"), ("dew", "fail"), ("weather", "warn"),
        ("disk", "fail"), disk=5,
    )  # fmt: skip
    assert [(p.key, p.text) for p in health.check(many, True, NOW)] == [
        ("disk", "Nur noch 5 % Speicher frei."),
        ("check_power", "power tun"),
        ("check_dew", "dew tun"),
    ]


async def test_camera_page_shows_setup_card(
    app: FastAPI, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    dev = FakeDevice()
    await paired_token(client, dev)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        await db.execute(
            update(Device)
            .where(Device.id == dev.device_id)
            .values(last_status={"checks": [{"id": "mask", "state": "warn", "detail": "Abwarten"}]})
        )
        await db.commit()
    page = (await client.get(f"/cameras/{dev.device_id}")).text
    assert "Einrichtung" not in page  # offline: the last status may be old

    def online(_device_id: str) -> bool:
        return True

    monkeypatch.setattr(app.state.connections, "is_online", online)
    page = (await client.get(f"/cameras/{dev.device_id}")).text
    assert "<h2>Einrichtung</h2>" in page
    assert "<strong>Himmelsmaske gelernt</strong>: Abwarten" in page
