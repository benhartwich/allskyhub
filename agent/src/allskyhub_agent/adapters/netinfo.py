"""What the camera's network looks like, for the local UI (roadmap L13). Read-only and
without privileges: `ip -j` (iproute2) and, if NetworkManager runs, `nmcli` for the
Wi-Fi name and signal. Every reader fails soft."""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any, cast

_SYS_NET = Path("/sys/class/net")


def _run(*args: str) -> str | None:
    exe = shutil.which(args[0])
    if exe is None:
        return None
    try:
        res = subprocess.run(  # noqa: S603 - fixed arguments
            [exe, *args[1:]], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return res.stdout if res.returncode == 0 else None


def _json(out: str | None) -> list[dict[str, Any]]:
    try:
        data: object = json.loads(out or "[]")
    except ValueError:
        return []
    return cast("list[dict[str, Any]]", data) if isinstance(data, list) else []


def interfaces(
    addr_json: str | None = None, route_json: str | None = None, sys_net: Path = _SYS_NET
) -> list[dict[str, Any]]:
    """`[{name, kind: "wifi"|"ethernet", up, addresses, default}]` without loopback and
    virtual links; `default` marks the one the default route leaves through."""
    links = _json(addr_json if addr_json is not None else _run("ip", "-j", "addr", "show"))
    routes = _json(
        route_json if route_json is not None else _run("ip", "-j", "route", "show", "default")
    )
    default = {str(r.get("dev")) for r in routes}
    out: list[dict[str, Any]] = []
    for link in links:
        name = str(link.get("ifname", ""))
        if not name or link.get("link_type") != "ether":
            continue
        if not (sys_net / name / "device").exists() and (sys_net / name).exists():
            continue  # bridges, veth, docker: no hardware behind them
        kind = "wifi" if (sys_net / name / "wireless").exists() else "ethernet"
        addrs = cast("list[dict[str, Any]]", link.get("addr_info") or [])
        out.append(
            {
                "name": name,
                "kind": kind,
                "up": link.get("operstate") == "UP",
                "addresses": [
                    f"{a.get('local')}/{a.get('prefixlen')}"
                    for a in addrs
                    if a.get("family") == "inet" or not str(a.get("local", "")).startswith("fe80")
                ],
                "default": name in default,
            }
        )
    return out


def wifi(nmcli_out: str | None = None) -> dict[str, Any] | None:
    """The joined Wi-Fi `{ssid, signal}` (signal 0..100), None without one."""
    out = (
        nmcli_out
        if nmcli_out is not None
        else _run("nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL", "dev", "wifi")
    )
    for line in (out or "").splitlines():
        # Terse output escapes ":" in values as "\:".
        parts = line.replace("\\:", "\x00").split(":")
        if len(parts) == 3 and parts[0] == "yes":
            try:
                signal = int(parts[2])
            except ValueError:
                signal = None
            return {"ssid": parts[1].replace("\x00", ":"), "signal": signal}
    return None


def summary() -> dict[str, Any]:
    return {"hostname": socket.gethostname(), "interfaces": interfaces(), "wifi": wifi()}
