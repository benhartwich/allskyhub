"""Setup file on the boot partition (SPEC §7.3)."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from allskyhub_agent.adapters.network import JoinError, Network

log = logging.getLogger(__name__)

DEFAULT_PATH = Path("/boot/firmware/allskyhub-setup.json")
MAX_BYTES = 64 * 1024
_COUNTRY = re.compile(r"^[A-Z]{2}$")


class SetupFileError(ValueError):
    pass


@dataclass(frozen=True)
class SetupFile:
    ssid: str | None
    password: str | None
    country: str | None
    hub_url: str | None


def parse(raw: bytes) -> SetupFile:
    if len(raw) > MAX_BYTES:
        raise SetupFileError("file too large")
    try:
        data = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SetupFileError("not valid JSON") from exc
    if not isinstance(data, dict) or data.get("allskyhub_setup") != 1:  # pyright: ignore[reportUnknownMemberType]
        raise SetupFileError("allskyhub_setup must be 1")
    d: dict[str, object] = data  # pyright: ignore[reportUnknownVariableType]
    ssid = password = country = hub = None
    wifi = d.get("wifi")
    if wifi is not None:
        if not isinstance(wifi, dict):
            raise SetupFileError("wifi must be an object")
        w: dict[str, object] = wifi  # pyright: ignore[reportUnknownVariableType]
        ssid, password = w.get("ssid"), w.get("password")
        if not isinstance(ssid, str) or not 1 <= len(ssid.encode()) <= 32:
            raise SetupFileError("wifi.ssid must have 1 to 32 bytes")
        if password in (None, ""):
            password = None
        elif not isinstance(password, str) or not 8 <= len(password) <= 63:
            raise SetupFileError("wifi.password must have 8 to 63 characters")
    c = d.get("wifi_country")
    if c is not None:
        if not isinstance(c, str) or not _COUNTRY.match(c.upper()):
            raise SetupFileError("wifi_country must be a two-letter code")
        country = c.upper()
    h = d.get("hub_url")
    if h is not None:
        if not isinstance(h, str) or not h.startswith(("https://", "http://")):
            raise SetupFileError("hub_url must be an http(s) URL")
        hub = h
    return SetupFile(ssid, password, country, hub)


def apply_once(path: Path, network: Network) -> SetupFile | None:
    """Read, apply and delete the setup file; rename it to *.failed.json if unusable.

    Returns what was applied (the caller stores `hub_url`), or None without a usable file.
    """
    if not path.exists():
        return None
    try:
        setup = parse(path.read_bytes())
    except (OSError, SetupFileError) as exc:
        log.warning("setup file unusable: %s", exc)
        path.replace(path.with_name("allskyhub-setup.failed.json"))
        return None
    if setup.ssid is not None:
        error: JoinError | None = network.join(setup.ssid, setup.password, setup.country or "00")
        if error is not None:
            log.warning("setup file: joining %r failed: %s", setup.ssid, error.value)
    path.unlink()
    return setup
