"""mDNS / DNS-SD announcement (SPEC §7.2): `_allskyhub._tcp` with TXT id and v."""

from __future__ import annotations

import socket

import ifaddr
from zeroconf import IPVersion, ServiceInfo, Zeroconf

SERVICE_TYPE = "_allskyhub._tcp.local."


def local_ipv4() -> list[str]:
    """IPv4 addresses of all interfaces except loopback."""
    out: list[str] = []
    for adapter in ifaddr.get_adapters():
        for ip in adapter.ips:
            if isinstance(ip.ip, str) and not ip.ip.startswith("127."):
                out.append(ip.ip)
    return sorted(set(out))


def host_name(device_id: str) -> str:
    return f"allskyhub-{device_id[:4].lower()}"


def service_info(device_id: str, port: int, addresses: list[str]) -> ServiceInfo:
    host = host_name(device_id)
    return ServiceInfo(
        SERVICE_TYPE,
        f"{host}.{SERVICE_TYPE}",
        port=port,
        properties={"id": device_id, "v": "1"},
        server=f"{host}.local.",
        addresses=[socket.inet_aton(a) for a in addresses],
    )


class Announcer:
    """Registers the service on all IPv4 interfaces; call `refresh()` after network changes."""

    def __init__(self, device_id: str, port: int) -> None:
        self._id = device_id
        self._port = port
        self._zc = Zeroconf(ip_version=IPVersion.V4Only)
        self._info: ServiceInfo | None = None

    def _addresses(self) -> list[str]:
        return local_ipv4()

    def refresh(self) -> None:
        info = service_info(self._id, self._port, self._addresses())
        if self._info is None:
            self._zc.register_service(info, allow_name_change=True)
        elif info.addresses != self._info.addresses:
            self._zc.update_service(info)
        self._info = info

    def close(self) -> None:
        if self._info is not None:
            self._zc.unregister_service(self._info)
        self._zc.close()
