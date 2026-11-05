"""Brute-force detection (Benjamin, 2026-11-05): when the per-account login limit trips
(auth/ratelimit.py ``AccountLockedError``), the account's owner gets a mail, at most every
6 hours, with a link to set a new password. The answer to whoever tried stays the same
429 whether the address has an account or not, and the mail goes out in the background so
its timing tells nothing either.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.auth import ratelimit
from allskyhub_server.mail.send import Mail, Mailer
from allskyhub_server.models import User

log = logging.getLogger(__name__)

_tasks: set[asyncio.Task[None]] = set()


async def warn(app: FastAPI, email: str, base_url: str) -> None:
    """Mail the owner of ``email`` (normalised) about the failed sign-ins, if it is an
    active account and none was sent in the last hours."""
    mailer: Mailer | None = getattr(app.state, "mailer", None)
    if mailer is None or not mailer.enabled:
        return
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        user = await db.scalar(select(User).where(User.email == email, User.is_active.is_(True)))
    if user is None:
        return
    try:
        await ratelimit.hit(app.state.engine, f"lockout-mail:{user.id}", ratelimit.LOCKOUT_MAIL)
    except ratelimit.RateLimitedError:
        return
    base = base_url.rstrip("/")
    body = (
        "Hallo,\n\nbei deinem allskyhub-Konto ist gerade sehr oft ein falsches Passwort "
        "eingegeben worden. Wir haben die Anmeldung deshalb für eine Weile gesperrt.\n\n"
        "Warst du das selbst, kannst du diese Nachricht ignorieren. Sonst versucht vielleicht "
        "jemand, dein Passwort zu erraten. Lege dann bitte ein neues fest, das du nirgends "
        f"sonst verwendest:\n\n{base}/password/forgot\n\nallskyhub\n"
    )
    await mailer.send(
        Mail(to=user.email, subject="allskyhub: viele falsche Anmeldungen", body=body)
    )


def warn_later(app: FastAPI, email: str, ip: str, base_url: str) -> None:
    """Start ``warn`` without waiting for it; log the lock (the address, never the password)."""
    log.warning("login: too many attempts for one account", extra={"ip": ip})

    async def run() -> None:
        try:
            await warn(app, email, base_url)
        except Exception:
            log.exception("lockout mail failed")

    task = asyncio.create_task(run())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
