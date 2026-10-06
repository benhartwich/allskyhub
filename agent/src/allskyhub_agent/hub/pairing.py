"""Pairing state shown on the local setup API (SPEC §6.2 step 3, §7)."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class SetupInfo:
    device_id: str
    hub_url: str
    profile: str
    agent_version: str
    paired: bool
    pairing_code: str | None
    expires_in: int | None
    connected: bool


class PairingState:
    """Written by the hub client, read by the local web server."""

    def __init__(self, device_id: str, hub_url: str, profile: str, agent_version: str) -> None:
        self._lock = threading.Lock()
        self._device_id = device_id
        self._hub_url = hub_url
        self._profile = profile
        self._version = agent_version
        self._paired = False
        self._code: str | None = None
        self._code_deadline: float | None = None
        self._connected = False

    def set_unpaired(self, code: str | None, expires_in: int | None, now: float) -> None:
        with self._lock:
            self._paired = False
            self._code = code
            self._code_deadline = now + expires_in if code and expires_in else None

    def set_paired(self) -> None:
        with self._lock:
            self._paired = True
            self._code = None
            self._code_deadline = None

    def set_connected(self, connected: bool) -> None:
        with self._lock:
            self._connected = connected

    def info(self, now: float | None = None) -> SetupInfo:
        t = time.monotonic() if now is None else now
        with self._lock:
            code, deadline = self._code, self._code_deadline
            if deadline is not None and t >= deadline:
                code, deadline = None, None
            return SetupInfo(
                device_id=self._device_id,
                hub_url=self._hub_url,
                profile=self._profile,
                agent_version=self._version,
                paired=self._paired,
                pairing_code=code,
                expires_in=round(deadline - t) if deadline is not None else None,
                connected=self._connected,
            )
