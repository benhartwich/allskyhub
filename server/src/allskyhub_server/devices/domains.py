"""Own domains for public pages (roadmap #17).

The hub runs without root, so it never touches nginx or certbot. It checks the name and
that the domain already points at the hub, then leaves a request file in
``<data>/domains/``; the root helper (deploy/domains/allskyhub-domains.sh, a systemd timer)
checks again, gets a certificate (certbot, nginx briefly stopped), writes the nginx server
and answers with ``<domain>.ok`` or ``<domain>.failed``. ``sync`` reads those answers.
Requests for the domain then reach the hub, which shows that camera's public page.
"""

from __future__ import annotations

import asyncio
import logging
import re
import socket
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from allskyhub_server.models import Device

log = logging.getLogger(__name__)

# Lower-case host names with at least one dot, no IP addresses, no trailing dot.
DOMAIN = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


class DomainError(Exception):
    """German message for the page."""


def normalize(value: str) -> str:
    value = value.strip().lower().removeprefix("https://").removeprefix("http://")
    return value.split("/", 1)[0].rstrip(".")


def _addresses(host: str) -> set[str]:
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
        return {str(info[4][0]) for info in infos}
    except OSError:
        return set()


async def points_here(domain: str, base_url: str) -> bool:
    """The domain resolves to (some of) the hub's own addresses."""
    hub = urlsplit(base_url).hostname or ""
    mine, theirs = await asyncio.gather(
        asyncio.to_thread(_addresses, hub), asyncio.to_thread(_addresses, domain)
    )
    return bool(theirs) and theirs <= mine


def spool(data_dir: Path) -> Path:
    return data_dir / "domains"


async def request(
    db: AsyncSession, device: Device, value: str, base_url: str, data_dir: Path
) -> None:
    """Check and ask the root helper for the domain; the caller commits."""
    domain = normalize(value)
    hub = urlsplit(base_url).hostname or ""
    if not DOMAIN.fullmatch(domain):
        raise DomainError("Das ist kein gültiger Domainname (z. B. himmel.example.org).")
    if domain == hub or domain.endswith("." + hub):
        raise DomainError("Bitte eine eigene Domain, nicht die des Hubs.")
    taken = await db.scalar(
        select(Device.id).where(Device.custom_domain == domain, Device.id != device.id)
    )
    if taken is not None:
        raise DomainError("Diese Domain wird schon für eine andere Kamera verwendet.")
    if device.public_slug is None:
        raise DomainError("Schalte zuerst die öffentliche Seite ein.")
    if not await points_here(domain, base_url):
        raise DomainError(
            f"{domain} zeigt noch nicht auf {hub}. Lege im DNS einen CNAME-Eintrag auf {hub} an "
            "und versuche es nach ein paar Minuten noch einmal."
        )
    folder = spool(data_dir)
    folder.mkdir(mode=0o750, exist_ok=True)
    for old in folder.glob(f"{domain}.*"):
        old.unlink(missing_ok=True)
    (folder / f"{domain}.request").write_text(domain + "\n")
    device.custom_domain = domain
    device.custom_domain_state = "pending"
    device.custom_domain_error = None


def remove(device: Device, data_dir: Path) -> None:
    """Ask the root helper to take the domain down; the caller commits."""
    if device.custom_domain is None:
        return
    folder = spool(data_dir)
    folder.mkdir(mode=0o750, exist_ok=True)
    (folder / f"{device.custom_domain}.remove").write_text(device.custom_domain + "\n")
    device.custom_domain_state = "removing"


async def sync(db: AsyncSession, data_dir: Path) -> None:
    """Take over the root helper's answers; the caller commits."""
    folder = spool(data_dir)
    if not folder.is_dir():
        return
    devices = await db.scalars(select(Device).where(Device.custom_domain.is_not(None)))
    for device in devices:
        domain = device.custom_domain or ""
        # Unpaired, or the public page switched off: the domain goes too.
        gone = device.owner_id is None or device.public_slug is None
        if gone and device.custom_domain_state in ("pending", "active", "failed"):
            remove(device, data_dir)
        ok, failed, removed = (folder / f"{domain}.{s}" for s in ("ok", "failed", "removed"))
        if device.custom_domain_state == "pending" and ok.is_file():
            device.custom_domain_state = "active"
            device.custom_domain_error = None
            ok.unlink(missing_ok=True)
        elif device.custom_domain_state == "pending" and failed.is_file():
            device.custom_domain_state = "failed"
            device.custom_domain_error = failed.read_text()[:300].strip() or "unbekannt"
            failed.unlink(missing_ok=True)
        elif device.custom_domain_state == "removing" and removed.is_file():
            device.custom_domain = None
            device.custom_domain_state = None
            device.custom_domain_error = None
            removed.unlink(missing_ok=True)


class HostMap:
    """Active domains → public slug, refreshed at most every minute (for the middleware)."""

    def __init__(self) -> None:
        self.slugs: dict[str, str] = {}
        self.loaded_at = 0.0

    async def load(self, db: AsyncSession) -> None:
        rows = await db.execute(
            select(Device.custom_domain, Device.public_slug).where(
                Device.custom_domain_state == "active",
                Device.public_slug.is_not(None),
                Device.owner_id.is_not(None),
            )
        )
        self.slugs = {d: s for d, s in rows.all() if d and s}


# Paths a custom domain may serve: its own public page and what that page loads.
_SHARED_PREFIXES = ("/static/", "/space-weather/", "/healthz")


def rewrite(path: str, slug: str) -> str | None:
    """The path to serve for a request on a custom domain, or None (404)."""
    if path == "/":
        return f"/sky/{slug}"
    if path == f"/sky/{slug}" or path.startswith(f"/sky/{slug}/"):
        return path
    if path.startswith(_SHARED_PREFIXES):
        return path
    return None


async def run_forever(maker: Any, data_dir: Path, hosts: HostMap) -> None:
    """Every 30 s: take over the root helper's answers and refresh the host map."""
    while True:
        try:
            async with maker() as db:
                await sync(db, data_dir)
                await db.commit()
                await hosts.load(db)
        except Exception:
            log.exception("custom domains: sync failed")
        await asyncio.sleep(30)
