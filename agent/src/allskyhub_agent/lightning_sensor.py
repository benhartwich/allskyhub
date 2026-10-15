"""Lightning sensor AS3935 (SPEC §7.12, roadmap L31).

The AS3935 hears a strike by its radio crackle up to about 40 km, by day and night, and
raises its interrupt line. The agent tunes the sensor's antenna once at start (the LC
oscillator divided by 128 is counted on the interrupt line for each tuning capacitor, the
one nearest 500 kHz wins), calibrates its RC oscillators, and then reads each interrupt:
a strike (estimated distance, energy), a disturber or too much noise.

A camera flash (`lightning` event, SPEC §6.4) is confirmed by the strikes the sensor heard
around it: `data.sensor_km`. A storm in which the sensor heard nothing at all is a false
alarm and gets an automatic label. Strikes also make Alpaca's SafetyMonitor unsafe.
"""

from __future__ import annotations

import contextlib
import logging
import math
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from allskyhub_agent.weather import I2cBus, SensorMissing, header_chip
from allskyhub_protocol import Event, EventKind, LightningSensorStatus

log = logging.getLogger(__name__)

ADDRESSES = ("0x01", "0x02", "0x03")
RETRY_S = 60.0
HEARTBEAT_S = 60.0
KEEP = timedelta(hours=6)  # strikes kept for confirming flashes
RECENT = timedelta(minutes=30)
SLACK = timedelta(seconds=60)  # SPEC §7.12: 60 s before start to 60 s after end
STORM_GAP = timedelta(minutes=30)  # SPEC §6.4: a longer gap starts a new storm
OUT_OF_RANGE = 0x3F
LCO_TARGET_HZ = 500_000.0
LCO_DIV = 128
TUNE_S = 0.1

# Registers (AS3935 datasheet).
R_AFE = 0x00  # AFE_GB [5:1], PWD [0]
R_NOISE = 0x01  # NF_LEV [6:4], WDTH [3:0]; 0x22 after a preset
R_INT = 0x03  # LCO_FDIV [7:6], MASK_DIST [5], INT [3:0]
R_ENERGY = 0x04  # 0x04 LSB, 0x05 MSB, 0x06 [4:0] MMSB
R_DISTANCE = 0x07  # [5:0]
R_TUNE = 0x08  # DISP_LCO [7], DISP_SRCO [6], DISP_TRCO [5], TUN_CAP [3:0]
R_PRESET = 0x3C
R_CALIB_RCO = 0x3D
DIRECT_COMMAND = 0x96
AFE_INDOOR = 0x12
AFE_OUTDOOR = 0x0E
INT_NOISE, INT_DISTURBER, INT_STRIKE = 0x01, 0x04, 0x08


class IrqPin(Protocol):
    def wait(self, timeout: float) -> bool: ...

    def count(self, seconds: float) -> int: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class Strike:
    at: datetime
    km: float | None  # None: out of range (heard, distance not estimated)
    energy: int


class As3935:
    """The chip's registers over I²C."""

    def __init__(self, bus: I2cBus, address: int) -> None:
        self._bus = bus
        self._addr = address

    def read(self, reg: int) -> int:
        return self._bus.read_i2c_block_data(self._addr, reg, 1)[0]

    def write(self, reg: int, value: int) -> None:
        self._bus.write_byte_data(self._addr, reg, value & 0xFF)

    def setup(self, indoor: bool) -> None:
        """Preset, check that it is there, set the front-end gain (SPEC §7.12)."""
        try:
            self.write(R_PRESET, DIRECT_COMMAND)
            time.sleep(0.002)
            noise = self.read(R_NOISE)
        except OSError as exc:
            raise SensorMissing(f"no AS3935 at {self._addr:#x}: {exc}") from None
        if noise != 0x22:
            raise SensorMissing(f"register 1 is {noise:#x} after a preset: no AS3935")
        self.write(R_AFE, (AFE_INDOOR if indoor else AFE_OUTDOOR) << 1)

    def tune(self, count: Callable[[float], int]) -> int | None:
        """Antenna tuning: the capacitor whose LC oscillator is nearest 500 kHz; None if the
        interrupt line stays silent (not wired)."""
        int_reg = self.read(R_INT)
        self.write(R_INT, (int_reg & 0x3F) | 0xC0)  # LCO_FDIV = 128
        best: tuple[float, int] | None = None
        heard = False
        try:
            for cap in range(16):
                self.write(R_TUNE, 0x80 | cap)  # DISP_LCO on the interrupt line
                n = count(TUNE_S)
                heard = heard or n > 0
                off = abs(n / TUNE_S * LCO_DIV - LCO_TARGET_HZ)
                if best is None or off < best[0]:
                    best = (off, cap)
        finally:
            self.write(R_TUNE, best[1] if best is not None else 0)
            self.write(R_INT, int_reg & 0x3F)
        return best[1] if heard and best is not None else None

    def calibrate_rco(self) -> None:
        self.write(R_CALIB_RCO, DIRECT_COMMAND)
        tune = self.read(R_TUNE) & 0x0F
        self.write(R_TUNE, 0x40 | tune)  # DISP_SRCO
        time.sleep(0.002)
        self.write(R_TUNE, tune)

    def interrupt(self) -> tuple[int, int | None, int]:
        """(INT, distance km or None, energy) after the interrupt line rose; read after
        2 ms as the datasheet asks."""
        time.sleep(0.002)
        kind = self.read(R_INT) & 0x0F
        if kind != INT_STRIKE:
            return kind, None, 0
        dist = self.read(R_DISTANCE) & 0x3F
        e = (
            self.read(R_ENERGY)
            | self.read(R_ENERGY + 1) << 8
            | (self.read(R_ENERGY + 2) & 0x1F) << 16
        )
        return kind, None if dist == OUT_OF_RANGE else dist, e

    def close(self) -> None:
        self._bus.close()


def open_as3935(address: str, bus: int = 1) -> As3935:
    from smbus2 import SMBus  # only on the camera

    try:
        return As3935(SMBus(bus), int(address, 16))
    except (OSError, FileNotFoundError) as exc:
        raise SensorMissing(f"no I2C bus {bus}: {exc}") from None


class GpiodIrq:
    """The interrupt line: rising edges, with a buffer big enough for antenna tuning."""

    def __init__(self, pin: int) -> None:
        import gpiod  # only on the camera
        from gpiod.line import Direction, Edge

        self._req = gpiod.request_lines(
            header_chip(),
            consumer="allskyhub-lightning",
            config={pin: gpiod.LineSettings(direction=Direction.INPUT, edge_detection=Edge.RISING)},
            event_buffer_size=2048,
        )

    def wait(self, timeout: float) -> bool:
        if not self._req.wait_edge_events(timedelta(seconds=timeout)):
            return False
        self._req.read_edge_events()
        return True

    def count(self, seconds: float) -> int:
        while self._req.wait_edge_events(timedelta(0)):
            self._req.read_edge_events()
        n = 0
        end = time.monotonic() + seconds
        while (left := end - time.monotonic()) > 0:
            if self._req.wait_edge_events(timedelta(seconds=left)):
                n += len(self._req.read_edge_events(2048))
        return n

    def close(self) -> None:
        self._req.release()


@dataclass(frozen=True)
class LightningSensorConfig:
    sensor: str = "off"  # off, as3935
    address: str = "0x03"
    irq_gpio: int | None = None
    indoor: bool = False


class LightningSensor:
    """Reads the AS3935 on its own thread; never slows capture."""

    def __init__(
        self,
        cfg: LightningSensorConfig,
        on_strike: Callable[[Strike], None] | None = None,
        open_chip: Callable[[], As3935] | None = None,
        open_irq: Callable[[], IrqPin] | None = None,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._cfg = cfg
        self._on_strike = on_strike
        self._open_chip = open_chip or (lambda: open_as3935(cfg.address))
        self._open_irq = open_irq or (lambda: GpiodIrq(_pin(cfg.irq_gpio)))
        self._clock = clock
        self._now = now
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._chip: As3935 | None = None
        self._irq: IrqPin | None = None
        self._next_try = -math.inf
        self._next_beat = math.inf
        self._strikes: deque[Strike] = deque()
        self._at: datetime | None = None
        self._ok_since: datetime | None = None
        self._error: str | None = None if cfg.irq_gpio is not None else "no_pin"
        self.disturbers = 0
        self.noise = 0

    @property
    def on(self) -> bool:
        return self._cfg.sensor != "off"

    @property
    def status(self) -> LightningSensorStatus | None:
        if not self.on:
            return None
        now = self._now()
        with self._lock:
            recent = [s for s in self._strikes if now - s.at <= RECENT]
            kms = [s.km for s in recent if s.km is not None]
            return LightningSensorStatus(
                at=self._at,
                strikes_30min=len(recent),
                nearest_km=min(kms) if kms else None,
                last_strike_at=self._strikes[-1].at if self._strikes else None,
                error=self._error,
            )

    def heard(self, start: datetime, end: datetime) -> tuple[bool, float | None] | None:
        """(heard any, nearest km) for strikes in start..end; None when the sensor was not
        working for the whole window (nothing can be concluded then)."""
        with self._lock:
            if self._ok_since is None or self._ok_since > start or self._error is not None:
                return None
            hits = [s for s in self._strikes if start <= s.at <= end]
        kms = [s.km for s in hits if s.km is not None]
        return bool(hits), (min(kms) if kms else None)

    def _fail(self, t: float, error: str, why: object) -> None:
        log.warning("lightning sensor: %s", why)
        self._close()
        self._next_try = t + RETRY_S
        with self._lock:
            self._error, self._ok_since = error, None

    def _open(self, t: float) -> bool:
        if self._chip is not None:
            return True
        if self._cfg.irq_gpio is None or t < self._next_try:
            return False
        try:
            chip = self._open_chip()
            self._chip = chip
            chip.setup(self._cfg.indoor)
            self._irq = irq = self._open_irq()
            cap = chip.tune(irq.count)
            chip.calibrate_rco()
        except SensorMissing as exc:
            self._fail(t, "not_found", exc)
            return False
        except (OSError, ValueError) as exc:
            self._fail(t, "bad_data", exc)
            return False
        if cap is None:
            self._fail(t, "no_interrupt", "the interrupt line stays silent; check its pin")
            return False
        log.info("lightning sensor ready, antenna capacitor %d", cap)
        now = self._now()
        with self._lock:
            self._error, self._at, self._ok_since = None, now, now
        self._next_beat = t + HEARTBEAT_S
        return True

    def _close(self) -> None:
        for part in (self._irq, self._chip):
            if part is not None:
                with contextlib.suppress(OSError):
                    part.close()
        self._irq = self._chip = None

    def step(self, wait_s: float = 5.0) -> None:
        """Open when due, wait for one interrupt, answer it; a heartbeat read a minute."""
        t = self._clock()
        if not self._open(t):
            return
        chip, irq = self._chip, self._irq
        if chip is None or irq is None:
            return
        try:
            if irq.wait(wait_s):
                kind, km, energy = chip.interrupt()
                self._handle(kind, km, energy)
            elif self._clock() >= self._next_beat:
                if chip.read(R_NOISE) & 0x80:  # bit 7 is always 0
                    raise ValueError("register 1 reads garbage")
                self._next_beat = self._clock() + HEARTBEAT_S
                with self._lock:
                    self._at = self._now()
        except OSError as exc:
            self._fail(self._clock(), "not_found", exc)
        except ValueError as exc:
            self._fail(self._clock(), "bad_data", exc)

    def _handle(self, kind: int, km: int | None, energy: int) -> None:
        now = self._now()
        with self._lock:
            self._at = now
        if kind == INT_STRIKE:
            strike = Strike(now, None if km is None else float(km), energy)
            with self._lock:
                self._strikes.append(strike)
                while self._strikes and now - self._strikes[0].at > KEEP:
                    self._strikes.popleft()
            log.info("lightning sensor: strike, %s", f"{km} km" if km is not None else "far")
            if self._on_strike is not None:
                self._on_strike(strike)
        elif kind == INT_DISTURBER:
            self.disturbers += 1
        elif kind == INT_NOISE:
            self.noise += 1

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self.step()
            except Exception:  # never stop the agent over this
                log.exception("lightning sensor step failed")
                self._stop.wait(RETRY_S)
            if self._chip is None:
                self._stop.wait(5.0)

    def start(self) -> None:
        if self.on:
            threading.Thread(target=self.run, name="lightning-sensor", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self._close()


def _pin(pin: int | None) -> int:
    if pin is None:
        raise ValueError("no interrupt pin set")
    return pin


# --- confirming the camera's flashes --------------------------------------------------------


@dataclass
class _Storm:
    events: list[Event] = field(default_factory=list[Event])
    heard: bool = False
    unknown: bool = False  # the sensor was not working for part of it
    last: datetime | None = None


class StormCheck:
    """SPEC §7.12: each flash gets `data.sensor_km` once its window has passed; a storm in
    which the sensor heard nothing gets an automatic label on every one of its flashes."""

    def __init__(
        self,
        heard: Callable[[datetime, datetime], tuple[bool, float | None] | None],
        amend: Callable[[Event, dict[str, Any]], Event],
        false_alarm: Callable[[Event], None],
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._heard = heard
        self._amend = amend
        self._false_alarm = false_alarm
        self._now = now
        self._lock = threading.Lock()
        self._pending: list[Event] = []
        self._storms: dict[str, _Storm] = {}
        self._stop = threading.Event()

    def add(self, event: Event) -> None:
        if event.kind is EventKind.LIGHTNING and not event.imported:
            with self._lock:
                self._pending.append(event)

    def tick(self) -> None:
        now = self._now()
        with self._lock:
            due = [e for e in self._pending if e.end + SLACK <= now]
            self._pending = [e for e in self._pending if e.end + SLACK > now]
        for e in due:
            storm = self._storms.setdefault(str(e.data.get("storm") or e.id), _Storm())
            found = self._heard(e.start - SLACK, e.end + SLACK)
            if found is None:
                storm.unknown = True
                storm.events.append(e)
            else:
                heard, km = found
                storm.heard = storm.heard or heard
                storm.events.append(self._amend(e, {"sensor_km": km}))
            storm.last = max(storm.last or e.end, e.end)
        with self._lock:
            waiting = {str(e.data.get("storm") or e.id) for e in self._pending}
        for key, storm in list(self._storms.items()):
            if key in waiting or storm.last is None or now - storm.last <= STORM_GAP:
                continue
            del self._storms[key]
            if not storm.heard and not storm.unknown:
                log.info("storm %s: the sensor heard no strike; %d false alarm(s)",
                         key, len(storm.events))  # fmt: skip
                for e in storm.events:
                    self._false_alarm(e)

    def run(self) -> None:
        while not self._stop.wait(30.0):
            try:
                self.tick()
            except Exception:
                log.exception("storm check failed")

    def start(self) -> None:
        threading.Thread(target=self.run, name="storm-check", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
