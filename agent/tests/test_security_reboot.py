"""Reboot by day for system security updates (SPEC §8)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from allskyhub_agent.update.reboot import SecurityReboot, due

TZ = ZoneInfo("Europe/Vienna")


def test_due_only_by_day_and_once() -> None:
    noon = datetime(2026, 10, 14, 12, 0, tzinfo=TZ)
    assert due(noon, 25.0, busy=False, updating=False, last=None)
    assert not due(noon, 8.0, busy=False, updating=False, last=None)  # sun too low
    assert not due(noon.replace(hour=9), 25.0, busy=False, updating=False, last=None)
    assert not due(noon.replace(hour=16), 25.0, busy=False, updating=False, last=None)
    assert not due(noon, 25.0, busy=True, updating=False, last=None)  # products
    assert not due(noon, 25.0, busy=False, updating=True, last=None)  # agent update
    assert not due(noon, 25.0, busy=False, updating=False, last=date(2026, 10, 14))
    assert due(noon, 25.0, busy=False, updating=False, last=date(2026, 10, 13))
    assert not due(noon, None, busy=False, updating=False, last=None)  # no location


def test_reboots_once_when_required(tmp_path: Path) -> None:
    flag = tmp_path / "reboot-required"
    asked: list[int] = []
    now = [datetime(2026, 10, 14, 10, 30, tzinfo=UTC)]  # 12:30 in Vienna
    sec = SecurityReboot(
        tmp_path / "security-reboot",
        tz=lambda: TZ,
        sun_deg=lambda t: 25.0,
        busy=lambda: False,
        updating=lambda: False,
        reboot=lambda: asked.append(1),
        flag=flag,
        now=lambda: now[0],
    )
    assert not sec.required
    assert not sec.check()
    flag.touch()
    assert sec.required
    assert sec.check()
    assert asked == [1]
    assert not sec.check()  # not twice the same day (the reboot may have been refused)
    assert (tmp_path / "security-reboot").read_text().strip() == "2026-10-14"
