"""HTTPS for the local UI (SPEC §7.6, roadmap L24)."""

from __future__ import annotations

import datetime as dt
import http.client
import ipaddress
import json
import ssl
from collections.abc import Iterator
from pathlib import Path

import pytest
from cryptography import x509

from allskyhub_agent.live import LiveState
from allskyhub_agent.web.server import TlsSetup, WebServer
from allskyhub_agent.web.tls import OWN_CERT, SELF_CERT, Certificates, self_signed

NOW = dt.datetime(2026, 10, 13, 20, 0, tzinfo=dt.UTC)
LAN = [ipaddress.ip_address("192.168.1.40")]
NAMES = ["allskyhub-ab12.local", "pi", "pi.local"]


def _sans(path: Path) -> list[str]:
    cert = x509.load_pem_x509_certificate(path.read_bytes())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    return sorted(san.get_values_for_type(x509.DNSName)) + [
        str(a) for a in san.get_values_for_type(x509.IPAddress)
    ]


def test_self_signed_is_kept_until_an_address_changes(tmp_path: Path) -> None:
    certs = Certificates(tmp_path / "tls")
    cert, key = certs.ensure(NAMES, LAN, NOW)
    assert cert.name == SELF_CERT
    assert key.stat().st_mode & 0o777 == 0o600
    assert _sans(cert) == [*NAMES, "192.168.1.40"]
    first = cert.read_bytes()
    assert certs.ensure(NAMES, LAN, NOW)[0].read_bytes() == first  # nothing changed
    moved = [ipaddress.ip_address("192.168.1.41")]
    assert certs.ensure(NAMES, moved, NOW)[0].read_bytes() != first
    assert "192.168.1.41" in _sans(cert)
    info = certs.info(NOW)
    assert info["kind"] == "self"
    assert info["sha256"].count(":") == 31


def test_own_certificate(tmp_path: Path) -> None:
    certs = Certificates(tmp_path / "tls")
    cert_pem, key_pem = self_signed(["sky.example.org"], [], NOW)
    other_cert, other_key = self_signed(["x"], [], NOW)
    assert certs.install_own(b"nonsense", key_pem, NOW) == "bad_certificate"
    assert certs.install_own(cert_pem, b"nonsense", NOW) == "bad_key"
    assert certs.install_own(cert_pem, other_key, NOW) == "key_mismatch"
    assert certs.install_own(cert_pem, key_pem, NOW + dt.timedelta(days=4000)) == "expired"
    assert certs.install_own(cert_pem, key_pem, NOW) is None
    assert certs.ensure(NAMES, LAN, NOW)[0].name == OWN_CERT
    assert certs.info(NOW)["names"] == ["sky.example.org"]
    # Expired: back to the camera's own certificate without asking.
    assert certs.ensure(NAMES, LAN, NOW + dt.timedelta(days=4000))[0].name == SELF_CERT
    certs.remove_own()
    assert certs.info(NOW)["kind"] == "self"
    assert other_cert  # only made for the mismatch


@pytest.fixture
def servers(tmp_path: Path) -> Iterator[tuple[WebServer, list[int]]]:
    certs = Certificates(tmp_path / "tls")
    ctx = certs.context(["localhost"], [ipaddress.ip_address("127.0.0.1")], NOW)
    changed: list[int] = []
    web = WebServer(LiveState(), "127.0.0.1", 0,
                    tls=TlsSetup(certs, True, port=0, on_change=lambda: changed.append(1)),
                    tls_context=ctx)  # fmt: skip
    web.start()
    yield web, changed
    web.stop()


def _client_ctx(tmp_path: Path) -> ssl.SSLContext:
    ctx = ssl.create_default_context(cafile=str(tmp_path / "tls" / SELF_CERT))
    ctx.check_hostname = True
    return ctx


def test_https_serves_and_http_redirects(
    servers: tuple[WebServer, list[int]], tmp_path: Path
) -> None:
    web, changed = servers
    assert web.tls_port is not None
    plain = http.client.HTTPConnection("127.0.0.1", web.port)
    plain.request("GET", "/api/status?x=1", headers={"Host": "pi.local:8080"})
    r = plain.getresponse()
    assert r.status == 308
    assert r.getheader("Location") == f"https://pi.local:{web.tls_port}/api/status?x=1"
    r.read()
    plain.request("GET", "/api/health")  # the updater and the watchdog stay on plain HTTP
    r = plain.getresponse()
    assert r.status == 200
    r.read()
    plain.request("GET", "/", headers={"X-Forwarded-Proto": "https"})  # a proxy on this host
    assert plain.getresponse().status == 200
    plain.close()

    tls = http.client.HTTPSConnection("localhost", web.tls_port, context=_client_ctx(tmp_path))
    tls.request("GET", "/api/tls")
    r = tls.getresponse()
    assert r.status == 200
    info = json.loads(r.read())
    assert (info["enabled"], info["kind"]) == (True, "self")
    tls.request("POST", "/api/tls", body=json.dumps({"cert": "x", "key": "y"}),
                headers={"Content-Type": "application/json"})  # fmt: skip
    r = tls.getresponse()
    assert (r.status, json.loads(r.read())["detail"]) == (400, "bad_certificate")
    tls.request("POST", "/api/tls/remove", body=b"{}")
    assert tls.getresponse().status == 202
    tls.close()
    assert changed == [1]


def test_off_by_default(tmp_path: Path) -> None:
    web = WebServer(LiveState(), "127.0.0.1", 0,
                    tls=TlsSetup(Certificates(tmp_path / "tls"), False))  # fmt: skip
    web.start()
    try:
        assert web.tls_port is None
        c = http.client.HTTPConnection("127.0.0.1", web.port)
        c.request("GET", "/api/tls")
        r = c.getresponse()
        assert (r.status, json.loads(r.read())) == (200, {"enabled": False, "port": 8443,
                                                           "kind": None})  # fmt: skip
        c.close()
    finally:
        web.stop()
