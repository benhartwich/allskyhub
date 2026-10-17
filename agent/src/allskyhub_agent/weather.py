"""Weather sensor and dew heater (SPEC §7.11, roadmap L30).

A BME280 on I²C gives temperature, humidity and pressure every 30 s; the dew point follows
with the Magnus formula. The dome heater on a GPIO pin keeps the dome free of dew: in
`auto` it heats while the air is within `heater_dew_margin_c` of its dew point or at least
90 % humid, without a working sensor whenever the camera is in night mode. The hardware sits
behind two small interfaces (`EnvSensor`, `HeaterPin`) so tests run with fakes; a failing
sensor is retried every minute and never slows capture.
"""

from __future__ import annotations

import contextlib
import logging
import math
import struct
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from allskyhub_protocol import EnvStatus, HeaterStatus

log = logging.getLogger(__name__)

READ_S = 30.0  # SPEC §7.11: a reading every 30 s
RETRY_S = 60.0  # a failed sensor or pin is tried again after a minute
SWITCH_MIN_S = 60.0  # the heater switches at most once a minute
HUMID_ON = 90.0  # % relative humidity that switches the heater on ...
HUMID_OFF = 85.0  # ... and below which it may switch off again
HYSTERESIS_C = 1.5
TOO_WARM_C = 45.0
HEATER_MODES = ("off", "auto", "on")
ENV_SENSORS = ("off", "bme280")
BME280_ADDRESSES = ("0x76", "0x77")


def dew_point(temperature_c: float, humidity_pct: float) -> float | None:
    """Dew point (°C) after Magnus (a = 17.62, b = 243.12 °C); None for 0 % humidity."""
    if humidity_pct <= 0:
        return None
    a, b = 17.62, 243.12
    g = math.log(min(humidity_pct, 100.0) / 100.0) + a * temperature_c / (b + temperature_c)
    return b * g / (a - g)


@dataclass(frozen=True)
class EnvReading:
    temperature_c: float
    humidity_pct: float
    pressure_hpa: float | None


class EnvSensor(Protocol):
    def read(self) -> EnvReading: ...

    def close(self) -> None: ...


class HeaterPin(Protocol):
    def set(self, on: bool) -> None: ...

    def close(self) -> None: ...


class SensorMissing(OSError):
    """No sensor answers at the address (`not_found`)."""


class BadData(ValueError):
    """The sensor answers with values no weather has (`bad_data`)."""


# --- BME280 -----------------------------------------------------------------------------


class I2cBus(Protocol):
    def read_i2c_block_data(self, i2c_addr: int, register: int, length: int) -> list[int]: ...

    def write_byte_data(self, i2c_addr: int, register: int, value: int) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class Bme280Calibration:
    t: tuple[int, int, int]
    p: tuple[int, int, int, int, int, int, int, int, int]
    h: tuple[int, int, int, int, int, int]

    @classmethod
    def parse(cls, block1: bytes, h1: int, block2: bytes) -> Bme280Calibration:
        """`block1` is 0x88..0x9F (24 bytes), `h1` 0xA1, `block2` 0xE1..0xE7 (7 bytes)."""
        t1, t2, t3, p1, p2, p3, p4, p5, p6, p7, p8, p9 = struct.unpack("<HhhHhhhhhhhh", block1)
        h2, h3, e4, e5, e6, h6 = struct.unpack("<hBBBBb", block2)
        h4 = (e4 << 4) | (e5 & 0x0F)
        h5 = (e6 << 4) | (e5 >> 4)
        h4 = h4 - 4096 if h4 > 2047 else h4
        h5 = h5 - 4096 if h5 > 2047 else h5
        return cls((t1, t2, t3), (p1, p2, p3, p4, p5, p6, p7, p8, p9), (h1, h2, h3, h4, h5, h6))

    def compensate(self, adc_t: int, adc_p: int, adc_h: int) -> EnvReading:
        """Bosch's floating point formulas (BME280 datasheet, section 8.1)."""
        t1, t2, t3 = self.t
        v1 = (adc_t / 16384.0 - t1 / 1024.0) * t2
        v2 = (adc_t / 131072.0 - t1 / 8192.0) ** 2 * t3
        t_fine = v1 + v2
        temperature = t_fine / 5120.0

        p1, p2, p3, p4, p5, p6, p7, p8, p9 = self.p
        v1 = t_fine / 2.0 - 64000.0
        v2 = v1 * v1 * p6 / 32768.0 + v1 * p5 * 2.0
        v2 = v2 / 4.0 + p4 * 65536.0
        v1 = (p3 * v1 * v1 / 524288.0 + p2 * v1) / 524288.0
        v1 = (1.0 + v1 / 32768.0) * p1
        pressure: float | None = None
        if v1 != 0:
            p = (1048576.0 - adc_p - v2 / 4096.0) * 6250.0 / v1
            p += (p9 * p * p / 2147483648.0 + p * p8 / 32768.0 + p7) / 16.0
            pressure = p / 100.0

        h1, h2, h3, h4, h5, h6 = self.h
        h = t_fine - 76800.0
        h = (adc_h - (h4 * 64.0 + h5 / 16384.0 * h)) * (
            h2 / 65536.0 * (1.0 + h6 / 67108864.0 * h * (1.0 + h3 / 67108864.0 * h))
        )
        h = h * (1.0 - h1 * h / 524288.0)
        return EnvReading(temperature, max(0.0, min(100.0, h)), pressure)


class Bme280:
    """A BME280 in forced mode: one measurement per `read()` (x1 oversampling, ~10 ms)."""

    CHIP_ID = 0x60

    def __init__(self, bus: I2cBus, address: int) -> None:
        self._bus = bus
        self._addr = address
        try:
            chip = bus.read_i2c_block_data(address, 0xD0, 1)[0]
        except OSError as exc:
            raise SensorMissing(f"no BME280 at {address:#x}: {exc}") from None
        if chip != self.CHIP_ID:
            raise SensorMissing(f"chip id {chip:#x} at {address:#x} is no BME280")
        self._cal = Bme280Calibration.parse(
            bytes(bus.read_i2c_block_data(address, 0x88, 24)),
            bus.read_i2c_block_data(address, 0xA1, 1)[0],
            bytes(bus.read_i2c_block_data(address, 0xE1, 7)),
        )
        bus.write_byte_data(address, 0xF2, 0x01)  # humidity x1 (applies with ctrl_meas)

    def read(self) -> EnvReading:
        self._bus.write_byte_data(self._addr, 0xF4, (1 << 5) | (1 << 2) | 0x01)  # forced
        deadline = time.monotonic() + 0.1
        while self._bus.read_i2c_block_data(self._addr, 0xF3, 1)[0] & 0x08:
            if time.monotonic() > deadline:
                raise OSError("BME280 measurement timed out")
            time.sleep(0.002)
        d = self._bus.read_i2c_block_data(self._addr, 0xF7, 8)
        adc_p = (d[0] << 12) | (d[1] << 4) | (d[2] >> 4)
        adc_t = (d[3] << 12) | (d[4] << 4) | (d[5] >> 4)
        adc_h = (d[6] << 8) | d[7]
        if adc_t == 0x80000 or adc_h == 0x8000:  # skipped measurement: not configured
            raise BadData("BME280 returned no measurement")
        r = self._cal.compensate(adc_t, adc_p, adc_h)
        if not -60.0 <= r.temperature_c <= 85.0:
            raise BadData(f"temperature {r.temperature_c:.1f} °C out of range")
        return r

    def close(self) -> None:
        self._bus.close()


def open_bme280(address: str, bus: int = 1) -> Bme280:
    """The real sensor on the Pi's I²C bus (`dtparam=i2c_arm=on`)."""
    from smbus2 import SMBus  # only on the camera

    try:
        handle = SMBus(bus)
    except (OSError, FileNotFoundError) as exc:
        raise SensorMissing(f"no I2C bus {bus}: {exc}") from None
    try:
        return Bme280(handle, int(address, 16))
    except Exception:
        handle.close()
        raise


# --- GPIO -----------------------------------------------------------------------------------


def header_chip() -> str:
    """The gpiochip of the 40-pin header: the Pi 4's pinctrl-bcm2711, the Pi 5's pinctrl-rp1
    (gpiochip0 or gpiochip4, depending on the kernel)."""
    import gpiod  # only on the camera

    for path in sorted(Path("/dev").glob("gpiochip*")):
        try:
            if not gpiod.is_gpiochip_device(str(path)):
                continue
            with gpiod.Chip(str(path)) as chip:
                info = chip.get_info()
        except OSError:
            continue
        if info.label.startswith("pinctrl-") and info.num_lines >= 28:
            return str(path)
    raise OSError("no GPIO header chip found")


class GpiodPin:
    """One output line held by the agent; `close()` switches it off and releases it."""

    def __init__(self, pin: int, active_low: bool, consumer: str = "allskyhub-heater") -> None:
        import gpiod  # only on the camera
        from gpiod.line import Direction, Value

        self._pin = pin
        self._value = Value
        self._req = gpiod.request_lines(
            header_chip(),
            consumer=consumer,
            config={
                pin: gpiod.LineSettings(
                    direction=Direction.OUTPUT, output_value=Value.INACTIVE, active_low=active_low
                )
            },
        )

    def set(self, on: bool) -> None:
        self._req.set_value(self._pin, self._value.ACTIVE if on else self._value.INACTIVE)

    def close(self) -> None:
        try:
            self.set(False)
        finally:
            self._req.release()


# --- the rule -------------------------------------------------------------------------------


def heater_wants(
    mode: str,
    reading: EnvReading | None,
    dewpoint_c: float | None,
    night: bool,
    is_on: bool,
    margin_c: float,
) -> tuple[bool, str]:
    """(on, reason) the heater should have now (SPEC §7.11), before the once-a-minute limit."""
    if reading is not None and reading.temperature_c > TOO_WARM_C:
        return False, "too_warm"
    if mode == "on":
        return True, "on"
    if mode != "auto":
        return False, "off"
    if reading is None:
        return (True, "night") if night else (False, "off")
    spread = reading.temperature_c - dewpoint_c if dewpoint_c is not None else math.inf
    if spread <= margin_c:
        return True, "dew"
    if reading.humidity_pct >= HUMID_ON:
        return True, "humidity"
    if is_on and (spread <= margin_c + HYSTERESIS_C or reading.humidity_pct >= HUMID_OFF):
        return True, "dew" if spread <= margin_c + HYSTERESIS_C else "humidity"
    return False, "off"


@dataclass(frozen=True)
class WeatherConfig:
    sensor: str = "off"  # ENV_SENSORS
    address: str = "0x76"
    heater: str = "off"  # HEATER_MODES
    heater_gpio: int | None = None
    heater_active_low: bool = False
    dew_margin_c: float = 3.0


class WeatherStation:
    """Reads the sensor and drives the heater on its own thread."""

    def __init__(
        self,
        cfg: WeatherConfig,
        night: Callable[[], bool],
        open_sensor: Callable[[], EnvSensor] | None = None,
        open_pin: Callable[[], HeaterPin] | None = None,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._cfg = cfg
        self._night = night
        self._open_sensor = open_sensor or (lambda: open_bme280(cfg.address))
        self._open_pin = open_pin or (
            lambda: GpiodPin(cast_pin(cfg.heater_gpio), cfg.heater_active_low)
        )
        self._clock = clock
        self._now = now
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._sensor: EnvSensor | None = None
        self._pin: HeaterPin | None = None
        self._next_read = -math.inf
        self._next_pin_try = -math.inf
        self._reading: EnvReading | None = None
        self._env: EnvStatus | None = EnvStatus() if self.sensor_on else None
        self._on = False
        self._reason = "off"
        self._switched = -math.inf

    @property
    def sensor_on(self) -> bool:
        return self._cfg.sensor != "off"

    @property
    def heater_on(self) -> bool:
        """The owner wants a heater and named its pin; nothing is switched otherwise."""
        return self._cfg.heater != "off" and self._cfg.heater_gpio is not None

    @property
    def env_status(self) -> EnvStatus | None:
        with self._lock:
            return self._env

    @property
    def heater_status(self) -> HeaterStatus | None:
        if not self.heater_on:
            return None
        with self._lock:
            return HeaterStatus(on=self._on, reason=self._reason)

    def _read(self, t: float) -> None:
        if not self.sensor_on or t < self._next_read:
            return
        error: str | None = None
        try:
            if self._sensor is None:
                self._sensor = self._open_sensor()
            r = self._sensor.read()
        except SensorMissing as exc:
            error, r = "not_found", None
            log.warning("weather sensor: %s", exc)
        except (OSError, BadData, ValueError) as exc:
            error, r = "bad_data", None
            log.warning("weather sensor: %s", exc)
        if r is None:
            if self._sensor is not None:
                with contextlib.suppress(OSError):
                    self._sensor.close()
                self._sensor = None
            self._next_read = t + RETRY_S
            with self._lock:
                self._reading = None
                old = self._env or EnvStatus()
                self._env = old.model_copy(update={"error": error})
            return
        self._next_read = t + READ_S
        dp = dew_point(r.temperature_c, r.humidity_pct)
        with self._lock:
            self._reading = r
            self._env = EnvStatus(
                at=self._now(),
                temperature_c=round(r.temperature_c, 1),
                humidity_pct=round(r.humidity_pct, 1),
                pressure_hpa=round(r.pressure_hpa, 1) if r.pressure_hpa is not None else None,
                dewpoint_c=round(dp, 1) if dp is not None else None,
            )

    def _drive(self, t: float) -> None:
        if not self.heater_on:
            return
        with self._lock:
            r = self._reading
            dp = self._env.dewpoint_c if self._env is not None and r is not None else None
            is_on = self._on
        want, reason = heater_wants(
            self._cfg.heater, r, dp, self._night(), is_on, self._cfg.dew_margin_c
        )
        if want != is_on and reason != "too_warm" and t - self._switched < SWITCH_MIN_S:
            return  # at most once a minute (the cut-off above 45 °C acts at once)
        if self._pin is None:
            if t < self._next_pin_try:
                return
            try:
                self._pin = self._open_pin()
            except (OSError, ValueError) as exc:
                self._next_pin_try = t + RETRY_S
                log.warning("heater pin %s: %s", self._cfg.heater_gpio, exc)
                return
        if want != is_on:
            try:
                self._pin.set(want)
            except OSError as exc:
                log.warning("heater pin %s: %s", self._cfg.heater_gpio, exc)
                return
            self._switched = t
            log.info("heater %s (%s)", "on" if want else "off", reason)
        with self._lock:
            self._on, self._reason = want, reason

    def step(self) -> None:
        """One round: read when due, then drive the heater."""
        t = self._clock()
        self._read(t)
        self._drive(t)

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self.step()
            except Exception:  # never stop the agent over this
                log.exception("weather station step failed")
            self._stop.wait(5.0)

    def start(self) -> None:
        if self.sensor_on or self.heater_on:
            threading.Thread(target=self.run, name="weather", daemon=True).start()

    def stop(self) -> None:
        """Off and released: a stopped agent never leaves the heater on."""
        self._stop.set()
        if self._pin is not None:
            try:
                self._pin.close()
            except OSError as exc:
                log.warning("heater pin %s: %s", self._cfg.heater_gpio, exc)
            self._pin = None
        if self._sensor is not None:
            with contextlib.suppress(OSError):
                self._sensor.close()
            self._sensor = None
        with self._lock:
            self._on, self._reason = False, "off"


def cast_pin(pin: int | None) -> int:
    if pin is None:
        raise ValueError("no heater pin set")
    return pin
