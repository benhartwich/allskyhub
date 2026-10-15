"""Lightning sensor AS3935 (SPEC §7.12, roadmap L31) with a fake chip and interrupt line."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from allskyhub_agent.lightning_sensor import (
    INT_DISTURBER,
    INT_STRIKE,
    As3935,
    LightningSensor,
    LightningSensorConfig,
    StormCheck,
    Strike,
)
from allskyhub_agent.weather import SensorMissing
from allskyhub_protocol import Event, EventKind

T0 = datetime(2026, 7, 23, 22, 0, tzinfo=UTC)


class FakeChip:
    """AS3935 registers; the LC oscillator runs at 480 kHz + 3 kHz per tuning capacitor."""

    def __init__(self, present: bool = True) -> None:
        self.present = present
        self.regs: dict[int, int] = {}
        self.pending: list[tuple[int, int, int]] = []  # (INT, distance, energy) to report

    def read_i2c_block_data(self, i2c_addr: int, register: int, length: int) -> list[int]:
        if not self.present:
            raise OSError(121, "Remote I/O error")
        if register == 0x03 and self.pending:
            kind, dist, energy = self.pending[0]
            self.regs[0x03] = (self.regs.get(0x03, 0) & 0xF0) | kind
            self.regs[0x07] = dist
            self.regs[0x04], self.regs[0x05], self.regs[0x06] = (
                energy & 0xFF, (energy >> 8) & 0xFF, (energy >> 16) & 0x1F,
            )  # fmt: skip
            self.pending.pop(0)
        return [self.regs.get(register + i, 0) for i in range(length)]

    def write_byte_data(self, i2c_addr: int, register: int, value: int) -> None:
        if not self.present:
            raise OSError(121, "Remote I/O error")
        if register == 0x3C:
            self.regs.update({0x00: 0x24, 0x01: 0x22, 0x02: 0xC2, 0x03: 0x00, 0x08: 0x00})
        else:
            self.regs[register] = value

    def close(self) -> None:
        pass

    def lco_hz(self) -> float:
        return 480_000 + 3_000 * (self.regs.get(0x08, 0) & 0x0F)


class FakeIrq:
    def __init__(self, chip: FakeChip, silent: bool = False) -> None:
        self.chip = chip
        self.silent = silent

    def wait(self, timeout: float) -> bool:
        return bool(self.chip.pending)

    def count(self, seconds: float) -> int:
        if self.silent or not self.chip.regs.get(0x08, 0) & 0x80:
            return 0
        return round(self.chip.lco_hz() / 128 * seconds)

    def close(self) -> None:
        pass


def test_setup_and_antenna_tuning() -> None:
    """SPEC §7.12: preset, outdoor gain, the capacitor nearest 500 kHz."""
    chip = FakeChip()
    a = As3935(chip, 0x03)
    a.setup(indoor=False)
    assert chip.regs[0x00] == 0x0E << 1
    cap = a.tune(FakeIrq(chip).count)
    assert cap == 7  # 501 kHz
    assert chip.regs[0x08] == 7  # DISP_LCO off again
    assert chip.regs[0x03] & 0xC0 == 0
    with pytest.raises(SensorMissing):
        As3935(FakeChip(present=False), 0x03).setup(indoor=False)
    assert As3935(FakeChip(), 0x03).tune(FakeIrq(chip, silent=True).count) is None


class Clock:
    def __init__(self) -> None:
        self.at = T0

    def now(self) -> datetime:
        return self.at

    def mono(self) -> float:
        return (self.at - T0).total_seconds()


def sensor(
    chip: FakeChip, clock: Clock, strikes: list[Strike], irq_gpio: int | None = 4
) -> LightningSensor:
    return LightningSensor(
        LightningSensorConfig(sensor="as3935", irq_gpio=irq_gpio),
        on_strike=strikes.append,
        open_chip=lambda: As3935(chip, 0x03),
        open_irq=lambda: FakeIrq(chip),
        clock=clock.mono,
        now=clock.now,
    )


def test_strikes_status_and_window() -> None:
    chip, clock, strikes = FakeChip(), Clock(), list[Strike]()
    s = sensor(chip, clock, strikes)
    s.step(0)  # opens, tunes, waits
    st = s.status
    assert st is not None
    assert st.error is None
    assert st.strikes_30min == 0
    clock.at = T0 + timedelta(minutes=5)
    chip.pending = [(INT_DISTURBER, 0, 0)]
    s.step(0)
    chip.pending = [(INT_STRIKE, 12, 40_000)]
    s.step(0)
    clock.at = T0 + timedelta(minutes=6)
    chip.pending = [(INT_STRIKE, 0x3F, 900)]  # out of range: heard, distance unknown
    s.step(0)
    assert [x.km for x in strikes] == [12.0, None]
    assert strikes[0].energy == 40_000
    assert s.disturbers == 1
    st = s.status
    assert st is not None
    assert (st.strikes_30min, st.nearest_km) == (2, 12.0)
    assert st.last_strike_at == T0 + timedelta(minutes=6)
    assert s.heard(T0 + timedelta(minutes=4), T0 + timedelta(minutes=5, seconds=30)) == (True, 12.0)
    assert s.heard(T0 + timedelta(minutes=7), T0 + timedelta(minutes=8)) == (False, None)
    assert s.heard(T0 - timedelta(minutes=1), T0 + timedelta(minutes=1)) is None  # not up yet
    clock.at = T0 + timedelta(minutes=40)
    st = s.status
    assert st is not None
    assert st.strikes_30min == 0


def test_errors_without_pin_or_chip() -> None:
    clock = Clock()
    s = sensor(FakeChip(), clock, [], irq_gpio=None)
    s.step(0)
    st = s.status
    assert st is not None
    assert st.error == "no_pin"
    gone = sensor(FakeChip(present=False), clock, [])
    gone.step(0)
    st = gone.status
    assert st is not None
    assert st.error == "not_found"
    off = LightningSensor(LightningSensorConfig())
    assert off.status is None


def flash(minute: float, storm: str = "storm-a") -> Event:
    t = T0 + timedelta(minutes=minute)
    return Event(id=f"lightning-{t:%Y%m%dT%H%M%S}Z", night_id="20260723", kind=EventKind.LIGHTNING,
                 start=t, end=t + timedelta(seconds=60), confidence=1.0, has_image=True,
                 data={"storm": storm})  # fmt: skip


def test_storm_check_confirms_and_labels() -> None:
    """SPEC §7.12: sensor_km on each flash; a storm with no strike at all is a false alarm."""
    clock = Clock()
    strikes = {T0 + timedelta(minutes=10, seconds=30): 8.0}

    def heard(a: datetime, b: datetime) -> tuple[bool, float | None] | None:
        kms = [km for at, km in strikes.items() if a <= at <= b]
        return bool(kms), (min(kms) if kms else None)

    amended: list[tuple[str, dict[str, Any]]] = []
    labelled: list[str] = []

    def amend(e: Event, data: dict[str, Any]) -> Event:
        amended.append((e.id, data))
        return e.model_copy(update={"data": {**e.data, **data}})

    check = StormCheck(heard, amend, lambda e: labelled.append(e.id), now=clock.now)
    real, lamp = flash(10, "storm-real"), flash(100, "storm-lamp")
    for e in (real, lamp, flash(105, "storm-lamp")):
        check.add(e)
    clock.at = T0 + timedelta(minutes=12)
    check.tick()
    assert amended == [(real.id, {"sensor_km": 8.0})]
    clock.at = T0 + timedelta(minutes=110)
    check.tick()
    assert [d for _, d in amended[1:]] == [{"sensor_km": None}, {"sensor_km": None}]
    assert labelled == []  # the storm may still go on
    clock.at = T0 + timedelta(minutes=140)
    check.tick()
    assert labelled == [lamp.id, flash(105).id]  # the real storm is never labelled


def test_storm_check_says_nothing_without_a_working_sensor() -> None:
    clock = Clock()
    labelled: list[str] = []
    check = StormCheck(lambda a, b: None, lambda e, d: e, lambda e: labelled.append(e.id),
                       now=clock.now)  # fmt: skip
    check.add(flash(0))
    clock.at = T0 + timedelta(hours=2)
    check.tick()
    check.tick()
    assert labelled == []
