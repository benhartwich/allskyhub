"""Reporter accounts (app "Himmelshelligkeit melden", Benjamin 2026-10-28): signed up on
/melden/konto behind Cloudflare Turnstile, confirmed by a link in an e-mail, then able to
sign in to the app and report; never to pair a camera (devices/pairing.py).

Signing up never tells whether an address already has an account: the page always says
that a mail is on its way, the password is hashed either way (equal timing), and an
existing account gets a mail saying so instead. An unconfirmed reporter who signs up again
gets a fresh confirmation link (its password stays the one first chosen, so nobody else can
set it by signing up with the address).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import secrets

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from allskyhub_server.auth.accounts import MIN_PASSWORD_LENGTH, AccountError, normalize_email
from allskyhub_server.auth.passwords import hash_secret_async
from allskyhub_server.models import EmailConfirmation, User

KIND = "reporter"
TTL = dt.timedelta(days=2)
SITEVERIFY = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def _hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


async def sign_up(db: AsyncSession, email: str, password: str) -> tuple[User, str | None]:
    """A new inactive reporter with a confirmation token, a new token for an unconfirmed
    reporter, or the existing account and None. Raises AccountError for an invalid address
    or password. The caller commits."""
    email = normalize_email(email)
    if "@" not in email or len(email) > 254 or email.startswith("@"):
        raise AccountError("Bitte eine gültige E-Mail-Adresse angeben.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AccountError(f"Das Passwort braucht mindestens {MIN_PASSWORD_LENGTH} Zeichen.")
    password_hash = await hash_secret_async(password)  # also when it is not used: timing
    existing = await db.scalar(select(User).where(User.email == email))
    if existing is not None and (existing.is_active or existing.kind != KIND):
        return existing, None
    if existing is not None:
        user = existing
        await db.execute(delete(EmailConfirmation).where(EmailConfirmation.user_id == user.id))
    else:
        user = User(email=email, password_hash=password_hash, kind=KIND, is_active=False)
        db.add(user)
        await db.flush()
    token = secrets.token_urlsafe(32)
    db.add(EmailConfirmation(token_hash=_hash(token), user_id=user.id, expires_at=_now() + TTL))
    return user, token


async def valid(db: AsyncSession, token: str) -> EmailConfirmation | None:
    """The open link, without using it (the page asks for a click first, so mail scanners
    that fetch links do not confirm)."""
    if not token or len(token) > 100:
        return None
    found = await db.get(EmailConfirmation, _hash(token))
    return found if found is not None and found.expires_at > _now() else None


async def confirm(db: AsyncSession, token: str) -> User | None:
    """Activate the account of a valid link (single use). The caller commits."""
    found = await valid(db, token)
    if found is None:
        return None
    user = await db.get(User, found.user_id)
    await db.delete(found)
    if user is None:
        return None
    user.is_active = True
    return user


async def purge(db: AsyncSession, now: dt.datetime) -> None:
    """Expired links go, and reporter accounts never confirmed with them."""
    expired = list(
        await db.scalars(
            select(EmailConfirmation.user_id).where(EmailConfirmation.expires_at < now)
        )
    )
    await db.execute(delete(EmailConfirmation).where(EmailConfirmation.expires_at < now))
    if expired:
        await db.execute(
            delete(User).where(User.id.in_(expired), User.kind == KIND, User.is_active.is_(False))
        )


class Turnstile:
    """Cloudflare Turnstile's server-side check of the widget's token."""

    def __init__(self, secret: str, client: httpx.AsyncClient | None = None) -> None:
        self._secret = secret
        self._client = client

    async def verify(self, token: str, ip: str) -> bool:
        if not token or len(token) > 2048:
            return False
        client = self._client or httpx.AsyncClient(timeout=10)
        try:
            r = await client.post(
                SITEVERIFY, data={"secret": self._secret, "response": token, "remoteip": ip}
            )
            return r.status_code == 200 and r.json().get("success") is True
        except (httpx.HTTPError, ValueError):
            return False
        finally:
            if self._client is None:
                await client.aclose()
