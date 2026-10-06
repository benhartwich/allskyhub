"""Setup mode state machine (SPEC §7.1).

`tick()` is called every few seconds with the current monotonic time; it decides when to open
and close the setup network. A network request from the app is only recorded by
`request_network()` (so the HTTP answer `202` goes out first) and carried out by the next
`tick()`.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from allskyhub_agent.adapters.network import JoinError, Network, WifiNetwork

log = logging.getLogger(__name__)

UNREACHABLE_S = 120.0  # configured Wi-Fi gone this long (and no Ethernet) → setup mode
IDLE_S = 900.0  # setup mode without requests this long → close it
INTERNET_WAIT_S = 30.0  # after joining, how long to wait for internet


def setup_ssid(device_id: str) -> str:
    return f"allskyhub-{device_id[:4].upper()}"


@dataclass(frozen=True)
class NetworkRequest:
    ssid: str
    password: str | None
    country: str
    hub_url: str | None


class SetupController:
    def __init__(
        self,
        network: Network,
        device_id: str,
        on_hub_url: Callable[[str], None] | None = None,
        sleep: Callable[[float], None] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._net = network
        self._ssid = setup_ssid(device_id)
        self._on_hub_url = on_hub_url
        self._sleep = sleep or time.sleep
        self._monotonic = monotonic or time.monotonic
        self._lock = threading.Lock()
        self._active = False
        self._last_request = 0.0
        self._offline_since: float | None = None
        self._pending: NetworkRequest | None = None
        self._last_error: JoinError | None = None
        self._started = False

    # --- read by the web server ----------------------------------------------------------
    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    @property
    def last_error(self) -> JoinError | None:
        with self._lock:
            return self._last_error

    def touch(self, now: float) -> None:
        """Any request on the setup network keeps setup mode open."""
        with self._lock:
            self._last_request = now

    def networks(self) -> list[WifiNetwork]:
        return self._net.scan()

    def request_network(self, req: NetworkRequest, now: float) -> None:
        with self._lock:
            self._pending = req
            self._last_request = now

    # --- driven by the setup thread -----------------------------------------------------
    def tick(self, now: float) -> None:
        with self._lock:
            pending, self._pending = self._pending, None
        if pending is not None:
            self._join(pending)
            return

        online = self._net.has_ethernet() or self._net.wifi_connected()
        if self.active:
            with self._lock:
                idle = now - self._last_request >= IDLE_S
            if idle:
                log.info("setup mode idle, closing the setup network")
                self._stop()
            return

        if online:
            self._offline_since = None
            self._started = True
            return
        if not self._started and not self._net.has_wifi_config():
            self._started = True
            self._start(now)  # first start without any network
            return
        self._started = True
        if self._offline_since is None:
            self._offline_since = now
        elif now - self._offline_since >= UNREACHABLE_S:
            self._start(now)

    def _start(self, now: float) -> None:
        log.info("opening setup network %s", self._ssid)
        self._net.start_hotspot(self._ssid)
        with self._lock:
            self._active = True
            self._last_request = now
        self._offline_since = None

    def _stop(self) -> None:
        self._net.stop_hotspot()
        with self._lock:
            self._active = False
        self._offline_since = None

    def _join(self, req: NetworkRequest) -> None:
        with self._lock:
            self._active = False
        error = self._net.join(req.ssid, req.password, req.country)
        if error is None:
            waited = 0.0
            while not self._net.has_internet() and waited < INTERNET_WAIT_S:
                self._sleep(2.0)
                waited += 2.0
            if not self._net.has_internet():
                error = JoinError.NO_INTERNET
        if error is not None:
            log.warning("joining %r failed: %s", req.ssid, error.value)
            with self._lock:
                self._last_error = error
            self._start(self._monotonic())
            return
        with self._lock:
            self._last_error = None
        log.info("joined %r", req.ssid)
        if req.hub_url and self._on_hub_url is not None:
            self._on_hub_url(req.hub_url)

    def run(self, stop: threading.Event, interval_s: float = 2.0) -> None:
        while not stop.is_set():
            try:
                self.tick(self._monotonic())
            except Exception:  # never let the setup thread die
                log.exception("setup tick failed")
            stop.wait(interval_s)
