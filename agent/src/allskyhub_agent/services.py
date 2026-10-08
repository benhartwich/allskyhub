"""Long-running helpers the CLI wires together: hub client with restart, mDNS refresh."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

import httpx

from allskyhub_agent import __version__
from allskyhub_agent.discovery import Announcer
from allskyhub_agent.hub.client import EventSource, HubClient, HubConfig, HubSession
from allskyhub_agent.hub.identity import DeviceIdentity
from allskyhub_agent.hub.pairing import PairingState
from allskyhub_agent.live import LiveState
from allskyhub_agent.products.build import newest_products
from allskyhub_agent.settings import AgentSettings
from allskyhub_agent.store.images import ImageStore
from allskyhub_protocol import Event, FrameInfo, Products, Status

log = logging.getLogger(__name__)


class HubManager:
    """Owns the running `HubClient`; `restart()` switches to another hub URL (SPEC §7.1)."""

    def __init__(
        self,
        identity: DeviceIdentity,
        pairing: PairingState,
        store: ImageStore,
        live: LiveState,
        profile: str,
        status: Callable[[], Status | None],
        settings_path: Path | None,
        events: EventSource | None = None,
    ) -> None:
        self._identity = identity
        self._pairing = pairing
        self._store = store
        self._live = live
        self._profile = profile
        self._status = status
        self._settings_path = settings_path
        self._events = events
        self._lock = threading.Lock()
        self._client: HubClient | None = None

    def start(self, hub_url: str) -> None:
        cfg = HubConfig(hub_url=hub_url)

        def make_session(http: httpx.AsyncClient) -> HubSession:
            return HubSession(
                cfg,
                self._identity,
                self._pairing,
                self._store,
                self._live,
                self._profile,
                __version__,
                self._status,
                http,
                latest_products=lambda: newest_products(self._store),
                events=self._events,
            )

        client = HubClient(make_session, cfg)
        client.start()
        with self._lock:
            self._client = client

    def restart(self, hub_url: str) -> None:
        log.info("switching to hub %s", hub_url)
        if self._settings_path is not None:
            AgentSettings(hub_url=hub_url).save(self._settings_path)
        self._pairing.set_hub_url(hub_url)
        self._pairing.set_unpaired(None, None, 0.0)  # a new hub pairs from scratch
        self.stop()
        self.start(hub_url)

    def notify_frame(self, info: FrameInfo) -> None:
        with self._lock:
            client = self._client
        if client is not None:
            client.notify_frame(info)

    def notify_event(self, event: Event) -> None:
        with self._lock:
            client = self._client
        if client is not None:
            client.notify_event(event)

    def notify_products(self, products: Products) -> None:
        with self._lock:
            client = self._client
        if client is not None:
            client.notify_products(products)

    def stop(self) -> None:
        with self._lock:
            client, self._client = self._client, None
        if client is not None:
            client.stop()


def run_announcer(announcer: Announcer, stop: threading.Event, interval_s: float = 30.0) -> None:
    """Keep the mDNS record's addresses current while networks come and go (SPEC §7.2)."""
    while not stop.is_set():
        try:
            announcer.refresh()
        except Exception:  # mDNS trouble must never stop the agent
            log.exception("mDNS refresh failed")
        stop.wait(interval_s)
    announcer.close()
