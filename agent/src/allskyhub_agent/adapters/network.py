"""Network control for setup mode (SPEC §7.1): scan, hotspot, join.

`NmcliNetwork` drives NetworkManager through `nmcli`; the agent user needs the polkit right
to manage NetworkManager (set up by the Pi image, M5). `SimNetwork` is for tests and laptops.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

SETUP_CONNECTION = "allskyhub-setup"
WIFI_CONNECTION = "allskyhub-wifi"
SETUP_ADDRESS = "10.42.0.1"


@dataclass(frozen=True)
class WifiNetwork:
    ssid: str
    signal: int  # 0-100
    secure: bool


class JoinError(StrEnum):
    """`last_error` values of SPEC §7.1."""

    WIFI_AUTH = "wifi_auth"
    WIFI_NOT_FOUND = "wifi_not_found"
    NO_INTERNET = "no_internet"


class Network(Protocol):
    def has_ethernet(self) -> bool: ...
    def has_wifi_config(self) -> bool: ...
    def wifi_connected(self) -> bool: ...
    def has_internet(self) -> bool: ...
    def scan(self) -> list[WifiNetwork]: ...
    def start_hotspot(self, ssid: str) -> None: ...
    def stop_hotspot(self) -> None: ...
    def join(self, ssid: str, password: str | None, country: str) -> JoinError | None: ...


def sort_networks(nets: list[WifiNetwork]) -> list[WifiNetwork]:
    """Strongest first, one entry per SSID, hidden (empty) SSIDs left out."""
    best: dict[str, WifiNetwork] = {}
    for n in nets:
        if n.ssid and (n.ssid not in best or n.signal > best[n.ssid].signal):
            best[n.ssid] = n
    return sorted(best.values(), key=lambda n: (-n.signal, n.ssid))


def _split_terse(line: str) -> list[str]:
    """Split one line of `nmcli -t` output: fields separated by ':', '\\:' escapes a colon."""
    out: list[str] = []
    cur: list[str] = []
    it = iter(line)
    for ch in it:
        if ch == "\\":
            cur.append(next(it, ""))
        elif ch == ":":
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return out


def parse_scan(output: str) -> list[WifiNetwork]:
    """Parse `nmcli -t -f SSID,SIGNAL,SECURITY dev wifi list`."""
    nets: list[WifiNetwork] = []
    for line in output.splitlines():
        parts = _split_terse(line)
        if len(parts) < 3:
            continue
        ssid, signal, security = parts[0], parts[1], parts[2]
        try:
            level = max(0, min(100, int(signal)))
        except ValueError:
            continue
        nets.append(WifiNetwork(ssid, level, security not in ("", "--")))
    return sort_networks(nets)


class NmcliNetwork:
    def __init__(self, ifname: str = "wlan0", nmcli: str = "nmcli") -> None:
        self._if = ifname
        self._nmcli = nmcli

    def _run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - fixed binary, no shell
            [self._nmcli, *args], capture_output=True, text=True, check=check, timeout=60
        )

    def _devices(self) -> list[list[str]]:
        out = self._run("-t", "-f", "DEVICE,TYPE,STATE", "dev").stdout
        return [_split_terse(line) for line in out.splitlines()]

    def has_ethernet(self) -> bool:
        return any(
            d[1] == "ethernet" and d[2] == "connected" for d in self._devices() if len(d) > 2
        )

    def wifi_connected(self) -> bool:
        out = self._run("-t", "-f", "NAME,DEVICE", "con", "show", "--active").stdout
        for line in out.splitlines():
            name, _, dev = line.rpartition(":")
            if dev == self._if and name != SETUP_CONNECTION:
                return True
        return False

    def has_internet(self) -> bool:
        res = self._run("-t", "networking", "connectivity", "check", check=False)
        return res.stdout.strip() == "full"

    def has_wifi_config(self) -> bool:
        out = self._run("-t", "-f", "NAME,TYPE", "con", "show").stdout
        return any(
            p[1] == "802-11-wireless" and p[0] != SETUP_CONNECTION
            for p in (_split_terse(line) for line in out.splitlines())
            if len(p) > 1
        )

    def scan(self) -> list[WifiNetwork]:
        res = self._run(
            "-t",
            "-f",
            "SSID,SIGNAL,SECURITY",
            "dev",
            "wifi",
            "list",
            "--rescan",
            "yes",
            check=False,
        )
        return parse_scan(res.stdout)

    def start_hotspot(self, ssid: str) -> None:
        self._run("con", "delete", SETUP_CONNECTION, check=False)
        self._run(
            "con", "add", "type", "wifi", "ifname", self._if, "con-name", SETUP_CONNECTION,
            "autoconnect", "no", "ssid", ssid,
            "802-11-wireless.mode", "ap", "802-11-wireless.band", "bg",
            "ipv4.method", "shared", "ipv4.addresses", f"{SETUP_ADDRESS}/24",
            "ipv6.method", "disabled",
        )  # fmt: skip
        self._run("con", "up", SETUP_CONNECTION)

    def stop_hotspot(self) -> None:
        self._run("con", "down", SETUP_CONNECTION, check=False)
        self._run("con", "delete", SETUP_CONNECTION, check=False)

    def join(self, ssid: str, password: str | None, country: str) -> JoinError | None:
        """Create the Wi-Fi connection and activate it.

        The password never appears on a command line: it goes through a 0600 temp file to
        `nmcli con up ... passwd-file`, and NetworkManager stores it with the connection.
        """
        subprocess.run(["iw", "reg", "set", country], capture_output=True, check=False)  # noqa: S603, S607
        self.stop_hotspot()
        self._run("con", "delete", WIFI_CONNECTION, check=False)
        args = ["con", "add", "type", "wifi", "ifname", self._if, "con-name", WIFI_CONNECTION]
        args += ["autoconnect", "yes", "ssid", ssid]
        if password:
            args += ["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk-flags", "0"]
        self._run(*args)
        fd, tmp = tempfile.mkstemp(prefix="allskyhub-psk-")
        try:
            with os.fdopen(fd, "w") as f:
                if password:
                    f.write(f"802-11-wireless-security.psk:{password}\n")
            res = self._run("con", "up", WIFI_CONNECTION, "passwd-file", tmp, check=False)
        finally:
            Path(tmp).unlink(missing_ok=True)
        if res.returncode == 0:
            return None
        msg = (res.stdout + res.stderr).lower()
        if "secrets were required" in msg or "802-1x" in msg or "psk" in msg:
            return JoinError.WIFI_AUTH
        return JoinError.WIFI_NOT_FOUND


class SimNetwork:
    """In-memory `Network` for tests and `--sim`."""

    def __init__(
        self,
        networks: list[WifiNetwork] | None = None,
        passwords: dict[str, str | None] | None = None,
        ethernet: bool = False,
    ) -> None:
        self.networks = networks or []
        self.passwords = passwords or {}
        self.ethernet = ethernet
        self.configured: str | None = None
        self.connected = False
        self.hotspot: str | None = None
        self.internet = True

    def has_internet(self) -> bool:
        return self.connected and self.internet

    def has_ethernet(self) -> bool:
        return self.ethernet

    def has_wifi_config(self) -> bool:
        return self.configured is not None

    def wifi_connected(self) -> bool:
        return self.connected

    def scan(self) -> list[WifiNetwork]:
        return sort_networks(self.networks)

    def start_hotspot(self, ssid: str) -> None:
        self.hotspot = ssid

    def stop_hotspot(self) -> None:
        self.hotspot = None

    def join(self, ssid: str, password: str | None, country: str) -> JoinError | None:
        self.hotspot = None
        if ssid not in self.passwords:
            return JoinError.WIFI_NOT_FOUND
        if self.passwords[ssid] != password:
            return JoinError.WIFI_AUTH
        self.configured = ssid
        self.connected = True
        return None
