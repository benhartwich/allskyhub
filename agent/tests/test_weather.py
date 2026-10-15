"""Weather sensor and dew heater (SPEC §7.11, roadmap L30) with a fake I²C bus and pin."""

from __future__ import annotations

import struct
from datetime import UTC, datetime

import pytest

from allskyhub_agent.weather import (
    Bme280,
    EnvReading,
    SensorMissing,
    WeatherConfig,
    WeatherStation,
    dew_point,
    heater_wants,
)

# The BMP280/BME280 datasheet's worked example: 25.08 °C and 100653.27 Pa.
T_CAL = (27504, 26435, -1000)
P_CAL = (36477, -10685, 3024, 2855, 140, -7, 15500, -14600, 6000)
H_CAL = (75, 362, 0, 313, 50, 30)  # typical values of real sensors
ADC_T, ADC_P = 519888, 415148


class FakeBus:
    """Registers of a BME280 in a dict."""

    def __init__(self, chip_id: int = 0x60, adc_h: int = 30000, fail: bool = False) -> None:
        self.fail = fail
        self.writes: list[tuple[int, int]] = []
        h1, h2, h3, h4, h5, h6 = H_CAL
        block1 = struct.pack("<HhhHhhhhhhhh", *T_CAL, *P_CAL)
        e4, e5, e6 = (h4 >> 4) & 0xFF, ((h5 & 0x0F) << 4) | (h4 & 0x0F), (h5 >> 4) & 0xFF
        block2 = struct.pack("<hBBBBb", h2, h3, e4, e5, e6, h6)
        data = [
            (ADC_P >> 12) & 0xFF, (ADC_P >> 4) & 0xFF, (ADC_P & 0x0F) << 4,
            (ADC_T >> 12) & 0xFF, (ADC_T >> 4) & 0xFF, (ADC_T & 0x0F) << 4,
            adc_h >> 8, adc_h & 0xFF,
        ]  # fmt: skip
        self.regs: dict[int, int] = {0xD0: chip_id, 0xA1: h1, 0xF3: 0}
        self.regs.update({0x88 + i: b for i, b in enumerate(block1)})
        self.regs.update({0xE1 + i: b for i, b in enumerate(block2)})
        self.regs.update({0xF7 + i: b for i, b in enumerate(data)})

    def read_i2c_block_data(self, i2c_addr: int, register: int, length: int) -> list[int]:
        if self.fail:
            raise OSError(121, "Remote I/O error")
        return [self.regs.get(register + i, 0) for i in range(length)]

    def write_byte_data(self, i2c_addr: int, register: int, value: int) -> None:
        self.writes.append((register, value))

    def close(self) -> None:
        pass


def test_bme280_datasheet_example() -> None:
    """SPEC §7.11: Bosch's compensation gives the datasheet's values."""
    r = Bme280(FakeBus(), 0x76).read()
    assert r.temperature_c == pytest.approx(25.08, abs=0.01)
    assert r.pressure_hpa is not None
    assert r.pressure_hpa == pytest.approx(1006.53, abs=0.02)
    assert 0 <= r.humidity_pct <= 100


def test_bme280_forced_mode_and_missing_sensor() -> None:
    bus = FakeBus()
    Bme280(bus, 0x76).read()
    assert (0xF2, 0x01) in bus.writes  # humidity oversampling x1
    assert bus.writes[-1] == (0xF4, 0b001_001_01)  # temperature x1, pressure x1, forced
    with pytest.raises(SensorMissing):
        Bme280(FakeBus(chip_id=0x58), 0x76)  # a BMP280: no humidity
    with pytest.raises(SensorMissing):
        Bme280(FakeBus(fail=True), 0x76)


def test_dew_point_magnus() -> None:
    dp = dew_point(20.0, 50.0)
    assert dp is not None
    assert dp == pytest.approx(9.26, abs=0.05)
    assert dew_point(10.0, 100.0) == pytest.approx(10.0, abs=0.01)
    assert dew_point(10.0, 0.0) is None


def _reading(t: float, rh: float) -> tuple[EnvReading, float | None]:
    return EnvReading(t, rh, 1013.0), dew_point(t, rh)


def test_heater_rule() -> None:
    """SPEC §7.11: dew margin, humidity, hysteresis, night without a sensor, 45 °C cut-off."""
    dry, dry_dp = _reading(10.0, 50.0)  # dew point about 0 °C: 10 °C apart
    wet, wet_dp = _reading(10.0, 85.0)  # dew point 7.6 °C: within 3 °C
    assert heater_wants("auto", dry, dry_dp, True, False, 3.0) == (False, "off")
    assert heater_wants("auto", wet, wet_dp, False, False, 3.0) == (True, "dew")
    humid, humid_dp = EnvReading(10.0, 92.0, None), 20.0 - 30.0  # dew point far below
    assert heater_wants("auto", humid, humid_dp, False, False, 3.0) == (True, "humidity")
    # Hysteresis: 3.5 °C apart stays on, 5 °C apart and dry switches off.
    near = EnvReading(10.0, 70.0, None)
    assert heater_wants("auto", near, 6.5, False, True, 3.0) == (True, "dew")
    assert heater_wants("auto", near, 6.5, False, False, 3.0) == (False, "off")
    assert heater_wants("auto", near, 5.0, False, True, 3.0) == (False, "off")
    # No sensor: heat at night only.
    assert heater_wants("auto", None, None, True, False, 3.0) == (True, "night")
    assert heater_wants("auto", None, None, False, True, 3.0) == (False, "off")
    assert heater_wants("on", dry, dry_dp, False, False, 3.0) == (True, "on")
    assert heater_wants("off", wet, wet_dp, True, True, 3.0) == (False, "off")
    hot = EnvReading(46.0, 95.0, None)
    assert heater_wants("on", hot, 45.0, True, True, 3.0) == (False, "too_warm")


class FakeSensor:
    def __init__(self, readings: list[EnvReading | Exception]) -> None:
        self.readings = readings
        self.closed = 0

    def read(self) -> EnvReading:
        r = self.readings.pop(0) if len(self.readings) > 1 else self.readings[0]
        if isinstance(r, Exception):
            raise r
        return r

    def close(self) -> None:
        self.closed += 1


class FakePin:
    def __init__(self) -> None:
        self.values: list[bool] = []
        self.closed = False

    def set(self, on: bool) -> None:
        self.values.append(on)

    def close(self) -> None:
        self.values.append(False)
        self.closed = True


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


AT = datetime(2026, 10, 15, 22, 0, tzinfo=UTC)


def test_station_reads_and_heats_at_most_once_a_minute() -> None:
    sensor = FakeSensor([EnvReading(10.0, 85.0, 1013.0)])
    pin = FakePin()
    clock = Clock()
    station = WeatherStation(
        WeatherConfig(sensor="bme280", heater="auto", heater_gpio=17),
        night=lambda: False,
        open_sensor=lambda: sensor,
        open_pin=lambda: pin,
        clock=clock,
        now=lambda: AT,
    )
    station.step()
    env = station.env_status
    assert env is not None
    assert env.temperature_c == 10.0
    assert env.dewpoint_c == pytest.approx(7.6, abs=0.1)
    assert env.at == AT
    assert station.heater_status is not None
    assert station.heater_status.on
    assert station.heater_status.reason == "dew"
    assert pin.values == [True]
    # Dry air 30 s later: the reading is due, but the heater may not switch yet.
    dry: list[EnvReading | Exception] = [EnvReading(10.0, 40.0, 1013.0)]
    sensor.readings = dry
    clock.t = 30.0
    station.step()
    assert pin.values == [True]
    clock.t = 61.0
    station.step()
    assert pin.values == [True, False]
    status = station.heater_status
    assert status is not None
    assert not status.on
    station.stop()
    assert pin.closed  # off and released


def test_station_without_working_sensor_heats_at_night_and_retries() -> None:
    """SPEC §7.11: a failing sensor is retried every minute; `auto` then heats at night."""
    opened: list[int] = []

    def broken() -> FakeSensor:
        opened.append(1)
        raise SensorMissing("no BME280 at 0x76")

    pin = FakePin()
    clock = Clock()
    station = WeatherStation(
        WeatherConfig(sensor="bme280", heater="auto", heater_gpio=17),
        night=lambda: True,
        open_sensor=broken,
        open_pin=lambda: pin,
        clock=clock,
    )
    station.step()
    env = station.env_status
    assert env is not None
    assert env.error == "not_found"
    assert env.temperature_c is None
    assert pin.values == [True]
    assert station.heater_status is not None
    assert station.heater_status.reason == "night"
    clock.t = 30.0
    station.step()
    assert len(opened) == 1  # not before a minute
    clock.t = 61.0
    station.step()
    assert len(opened) == 2


def test_nothing_switched_without_a_pin() -> None:
    """SPEC §7.11: no heater_gpio, no switching and no heater status."""
    station = WeatherStation(
        WeatherConfig(heater="on", heater_gpio=None),
        night=lambda: True,
        open_pin=lambda: pytest.fail("no pin may be opened"),
    )
    station.step()
    assert station.heater_status is None
    assert station.env_status is None
