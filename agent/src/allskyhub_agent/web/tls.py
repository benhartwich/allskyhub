"""HTTPS for the local UI (SPEC §7.6, roadmap L24).

The agent makes a self-signed certificate (ECDSA P-256, 10 years) for its host names
(`<host>`, `<host>.local`, the mDNS name) and its current addresses, and makes it again
when an address changes.
The owner can upload an own certificate and key instead; it is used while it is valid.
Everything lives in `<data>/tls/`, keys with mode 0600.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import os
import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

SELF_CERT, SELF_KEY = "self.crt", "self.key"
OWN_CERT, OWN_KEY = "own.crt", "own.key"
VALID_DAYS = 3650
_MAX_PEM = 64 * 1024

Address = ipaddress.IPv4Address | ipaddress.IPv6Address


def _write(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    os.fchmod(fd, mode)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    tmp.replace(path)


def _names(cert: x509.Certificate) -> tuple[set[str], set[Address]]:
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return set(), set()
    ips: set[Address] = {
        a
        for a in san.get_values_for_type(x509.IPAddress)
        if isinstance(a, ipaddress.IPv4Address | ipaddress.IPv6Address)
    }
    return set(san.get_values_for_type(x509.DNSName)), ips


def self_signed(
    names: list[str], addresses: list[Address], now: dt.datetime
) -> tuple[bytes, bytes]:
    """(certificate PEM, key PEM) for the host names and the addresses."""
    key = ec.generate_private_key(ec.SECP256R1())
    dns = names or ["allskyhub.local"]
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, dns[0])])
    san = [x509.DNSName(n) for n in dns] + [x509.IPAddress(a) for a in addresses]
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=VALID_DAYS))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return cert.public_bytes(serialization.Encoding.PEM), key_pem


def check_pair(cert_pem: bytes, key_pem: bytes, now: dt.datetime) -> str | None:
    """Why an uploaded certificate and key cannot be used, or None if they can."""
    if len(cert_pem) > _MAX_PEM or len(key_pem) > _MAX_PEM:
        return "too_large"
    try:
        cert = x509.load_pem_x509_certificate(cert_pem)
    except ValueError:
        return "bad_certificate"
    try:
        key = serialization.load_pem_private_key(key_pem, password=None)
    except (ValueError, TypeError):
        return "bad_key"
    pub = serialization.PublicFormat.SubjectPublicKeyInfo
    enc = serialization.Encoding.DER
    if key.public_key().public_bytes(enc, pub) != cert.public_key().public_bytes(enc, pub):
        return "key_mismatch"
    if not cert.not_valid_before_utc <= now <= cert.not_valid_after_utc:
        return "expired"
    return None


@dataclass(frozen=True)
class Certificates:
    """The certificates in `<data>/tls/`."""

    folder: Path

    def install_own(self, cert_pem: bytes, key_pem: bytes, now: dt.datetime) -> str | None:
        why = check_pair(cert_pem, key_pem, now)
        if why is None:
            _write(self.folder / OWN_KEY, key_pem, 0o600)
            _write(self.folder / OWN_CERT, cert_pem, 0o644)
        return why

    def remove_own(self) -> None:
        (self.folder / OWN_CERT).unlink(missing_ok=True)
        (self.folder / OWN_KEY).unlink(missing_ok=True)

    def _own_ok(self, now: dt.datetime) -> bool:
        try:
            cert, key = (self.folder / OWN_CERT).read_bytes(), (self.folder / OWN_KEY).read_bytes()
        except OSError:
            return False
        return check_pair(cert, key, now) is None

    def ensure(
        self, names: list[str], addresses: list[Address], now: dt.datetime
    ) -> tuple[Path, Path]:
        """(certificate, key) to serve: the owner's while valid, else the self-signed one,
        made again when the names or addresses changed or it runs out within 30 days."""
        if self._own_ok(now):
            return self.folder / OWN_CERT, self.folder / OWN_KEY
        cert_path, key_path = self.folder / SELF_CERT, self.folder / SELF_KEY
        try:
            cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
            dns, ips = _names(cert)
            fresh = (
                key_path.is_file()
                and cert.not_valid_after_utc - now > dt.timedelta(days=30)
                and set(addresses) <= ips
                and set(names) <= dns
            )
        except (OSError, ValueError):
            fresh = False
        if not fresh:
            cert_pem, key_pem = self_signed(names, addresses, now)
            _write(key_path, key_pem, 0o600)
            _write(cert_path, cert_pem, 0o644)
        return cert_path, key_path

    def context(
        self, names: list[str], addresses: list[Address], now: dt.datetime
    ) -> ssl.SSLContext:
        cert, key = self.ensure(names, addresses, now)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(cert, key)
        return ctx

    def info(self, now: dt.datetime) -> dict[str, Any]:
        """What the local UI shows: whose certificate, until when, its fingerprint."""
        own = self._own_ok(now)
        path = self.folder / (OWN_CERT if own else SELF_CERT)
        try:
            cert = x509.load_pem_x509_certificate(path.read_bytes())
        except (OSError, ValueError):
            return {"kind": None}
        dns, ips = _names(cert)
        return {
            "kind": "own" if own else "self",
            "not_after": cert.not_valid_after_utc.isoformat(),
            "sha256": cert.fingerprint(hashes.SHA256()).hex(":"),
            "names": sorted(dns) + sorted(str(a) for a in ips),
        }
